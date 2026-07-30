"""节点统计特征提取模块 - 用于构建长期画像

提取的统计特征包括：
1. ADF平稳性检验 - 检测时间序列是否平稳
2. ACF/PACF特征 - 检测周期性和自相关性
3. 趋势特征 - 线性趋势斜率
4. 基本统计特征 - 均值、标准差、偏度、峰度等
5. 波动性特征 - 变化率、变异系数等
"""

import numpy as np
import numpy.typing as npt
from typing import Literal
from dataclasses import dataclass


@dataclass
class NodeProfile:
    """节点画像特征"""
    # 基本统计特征 (6个指标 * 5个统计量 = 30维)
    means: npt.NDArray[np.float32]      # 均值
    stds: npt.NDArray[np.float32]       # 标准差
    skews: npt.NDArray[np.float32]      # 偏度
    kurtoses: npt.NDArray[np.float32]  # 峰度
    mins: npt.NDArray[np.float32]       # 最小值
    maxs: npt.NDArray[np.float32]       # 最大值

    # 趋势特征 (6 * 1 = 6维)
    trend_slopes: npt.NDArray[np.float32]  # 线性趋势斜率

    # 平稳性特征 (6 * 1 = 6维)
    adf_stats: npt.NDArray[np.float32]     # ADF统计量

    # 周期性特征 (6 * 2 = 12维)
    acf_peaks: npt.NDArray[np.float32]      # ACF主峰强度
    periodicity_scores: npt.NDArray[np.float32]  # 周期性评分

    # 波动性特征 (6 * 2 = 12维)
    cv: npt.NDArray[np.float32]            # 变异系数
    change_rates: npt.NDArray[np.float32]  # 平均变化率

    # 总维度: 30 + 6 + 6 + 12 + 12 = 66维
    @property
    def total_dim(self) -> int:
        return (self.means.shape[0] * 5 +  # 基本统计
                self.trend_slopes.shape[0] +  # 趋势
                self.adf_stats.shape[0] +  # 平稳性
                self.acf_peaks.shape[0] + self.periodicity_scores.shape[0] +  # 周期性
                self.cv.shape[0] + self.change_rates.shape[0])  # 波动性

    def to_numpy(self) -> npt.NDArray[np.float32]:
        """转换为一维numpy数组"""
        return np.concatenate([
            self.means, self.stds, self.skews, self.kurtoses, self.mins, self.maxs,
            self.trend_slopes,
            self.adf_stats,
            self.acf_peaks, self.periodicity_scores,
            self.cv, self.change_rates,
        ])

    @classmethod
    def from_timeseries(
        cls,
        timeseries: npt.NDArray[np.float32],
        metric_names: list[str] | None = None,
    ) -> "NodeProfile":
        """从时间序列数据构建节点画像

        Args:
            timeseries: (T, D) 形状的时间序列，T为时间步数，D为指标数
            metric_names: 指标名称列表（可选）

        Returns:
            NodeProfile对象
        """
        T, D = timeseries.shape

        # 1. 基本统计特征
        means = np.mean(timeseries, axis=0)
        stds = np.std(timeseries, axis=0)
        skews = cls._compute_skewness(timeseries)
        kurtoses = cls._compute_kurtosis(timeseries)
        mins = np.min(timeseries, axis=0)
        maxs = np.max(timeseries, axis=0)

        # 2. 趋势特征
        trend_slopes = cls._compute_trend_slope(timeseries)

        # 3. 平稳性特征 (ADF)
        adf_stats = cls._compute_adf_stat(timeseries)

        # 4. 周期性特征
        acf_peaks, periodicity_scores = cls._compute_periodicity(timeseries)

        # 5. 波动性特征
        cv = cls._compute_cv(timeseries)
        change_rates = cls._compute_change_rate(timeseries)

        return cls(
            means=means.astype(np.float32),
            stds=stds.astype(np.float32),
            skews=skews.astype(np.float32),
            kurtoses=kurtoses.astype(np.float32),
            mins=mins.astype(np.float32),
            maxs=maxs.astype(np.float32),
            trend_slopes=trend_slopes.astype(np.float32),
            adf_stats=adf_stats.astype(np.float32),
            acf_peaks=acf_peaks.astype(np.float32),
            periodicity_scores=periodicity_scores.astype(np.float32),
            cv=cv.astype(np.float32),
            change_rates=change_rates.astype(np.float32),
        )

    @staticmethod
    def _compute_skewness(x: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
        """计算偏度"""
        mean = np.mean(x, axis=0, keepdims=True)
        std = np.std(x, axis=0, keepdims=True) + 1e-8
        z = (x - mean) / std
        skew = np.mean(z ** 3, axis=0)
        return skew

    @staticmethod
    def _compute_kurtosis(x: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
        """计算峰度 (超额峰度)"""
        mean = np.mean(x, axis=0, keepdims=True)
        std = np.std(x, axis=0, keepdims=True) + 1e-8
        z = (x - mean) / std
        kurt = np.mean(z ** 4, axis=0) - 3
        return kurt

    @staticmethod
    def _compute_trend_slope(x: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
        """计算线性趋势斜率"""
        T = x.shape[0]
        t = np.arange(T)
        t_mean = (T - 1) / 2

        # 归一化t
        t_norm = (t - t_mean) / T

        # 使用最小二乘法计算斜率
        x_mean = np.mean(x, axis=0, keepdims=True)
        numerator = np.sum(t_norm[:, np.newaxis] * (x - x_mean), axis=0)
        denominator = np.sum(t_norm ** 2) + 1e-8

        slopes = numerator / denominator
        return slopes

    @staticmethod
    def _compute_adf_stat(x: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
        """计算ADF统计量的简化版本

        使用增广迪基- fuller检验的简化实现
        返回: ADF统计量（越负越可能平稳）
        """
        T, D = x.shape
        if T < 20:
            return np.zeros(D, dtype=np.float32)

        # 使用一阶差分进行检验
        diff_x = np.diff(x, axis=0)  # (T-1, D)
        lag_x = x[:-1, :]            # (T-1, D)

        # 简化ADF: 检验 delta(x) = alpha + beta * x(t-1) + error
        # 使用OLS简化计算
        x_mean = np.mean(lag_x, axis=0, keepdims=True)
        diff_mean = np.mean(diff_x, axis=0, keepdims=True)

        # 计算beta (自回归系数)
        x_centered = lag_x - x_mean
        diff_centered = diff_x - diff_mean

        cov = np.mean(lag_x * diff_x, axis=0)
        var = np.var(lag_x, axis=0) + 1e-8

        beta = cov / var

        # 计算ADF统计量
        # 构建残差序列
        residuals = diff_x - diff_mean - beta * (lag_x - x_mean)

        # 计算标准误差
        se = np.sqrt(np.mean(residuals ** 2, axis=0) + 1e-8)

        # ADF统计量 (简化版)
        adf_stat = diff_mean / (se + 1e-8) * np.sqrt(T)

        # 也考虑beta的影响（接近0表示可能平稳）
        # 组合: ADF = -beta * sqrt(T) (当beta接近-1时序列更平稳)
        adf_combined = -beta * np.sqrt(T)

        # 裁剪到合理范围
        adf_combined = np.clip(adf_combined, -30, 30)

        return adf_combined.astype(np.float32)

    @staticmethod
    def _compute_periodicity(x: npt.NDArray[np.float32], max_lag: int = 100) -> tuple[
        npt.NDArray[np.float32], npt.NDArray[np.float32]
    ]:
        """计算周期性特征

        Returns:
            (acf_peaks, periodicity_scores)
            - acf_peaks: ACF在主要周期点的值
            - periodicity_scores: 周期性评分 (0-1)
        """
        T, D = x.shape
        max_lag = min(max_lag, T // 2 - 1)

        acf_peaks = np.zeros(D, dtype=np.float32)
        periodicity_scores = np.zeros(D, dtype=np.float32)

        for d in range(D):
            series = x[:, d]
            if np.std(series) < 1e-8:
                continue

            # 标准化
            series_norm = (series - np.mean(series)) / (np.std(series) + 1e-8)

            # 计算ACF
            acf = np.correlate(series_norm, series_norm, mode='full')
            acf = acf[T - 1:T - 1 + max_lag + 1]
            acf = acf / acf[0] if acf[0] != 0 else acf

            # 找峰值（排除lag=0）
            # 搜索多个可能的周期
            acf_peaks[d] = float(np.max(acf[1:min(24, max_lag)]))

            # 计算周期性评分：比较短期和长期的ACF
            # 强周期性：在特定lag处有明显的峰值
            # 短期相关 (lag 1-6)
            short_term = np.mean(acf[1:7])
            # 中期相关 (lag 24-48, 约2-4小时)
            mid_term = np.mean(acf[24:49]) if max_lag >= 48 else 0

            # 周期性评分 = 中期相关 / (短期相关 + 1e-8)
            periodicity_scores[d] = mid_term / (short_term + 1e-8)
            periodicity_scores[d] = np.clip(periodicity_scores[d], 0, 1)

        return acf_peaks, periodicity_scores

    @staticmethod
    def _compute_cv(x: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
        """计算变异系数 (Coefficient of Variation)"""
        mean = np.mean(x, axis=0)
        std = np.std(x, axis=0)
        cv = std / (np.abs(mean) + 1e-8)
        return cv.astype(np.float32)

    @staticmethod
    def _compute_change_rate(x: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
        """计算平均变化率"""
        diff = np.abs(np.diff(x, axis=0))
        mean_diff = np.mean(diff, axis=0)

        # 归一化到原始数据范围
        mean_val = np.mean(x, axis=0)
        change_rate = mean_diff / (np.abs(mean_val) + 1e-8)

        return change_rate.astype(np.float32)


def extract_profile_features(
    timeseries: npt.NDArray[np.float32],
) -> npt.NDArray[np.float32]:
    """便捷函数：从时间序列提取节点画像特征

    Args:
        timeseries: (T, D) 形状的时间序列

    Returns:
        (D * 11,) 形状的特征向量
    """
    profile = NodeProfile.from_timeseries(timeseries)
    return profile.to_numpy()


def compute_business_cycle_awareness(
    timeseries: npt.NDArray[np.float32],
    window_start: int,
    window_end: int,
) -> dict[str, float]:
    """计算业务周期感知特征

    用于区分"预期的业务周期性高峰"与"非预期的底层变更扰动"

    Args:
        timeseries: (T, D) 形状的完整时间序列
        window_start: 变更窗口开始索引
        window_end: 变更窗口结束索引

    Returns:
        包含业务周期感知特征的字典
    """
    T, D = timeseries.shape

    # 1. 窗口前后的周期性强度对比
    pre_window = timeseries[:window_start] if window_start > 0 else None
    post_window = timeseries[window_end:] if window_end < T else None

    result = {}

    for d in range(D):
        metric = timeseries[:, d]

        # 计算窗口前的周期性
        if pre_window is not None and len(pre_window) > 50:
            _, period_pre = NodeProfile._compute_periodicity(
                pre_window[:, d:d+1], max_lag=min(100, len(pre_window)//2)
            )
            result[f'periodicity_pre_{d}'] = float(period_pre[0])
        else:
            result[f'periodicity_pre_{d}'] = 0.0

        # 计算窗口后的周期性
        if post_window is not None and len(post_window) > 50:
            _, period_post = NodeProfile._compute_periodicity(
                post_window[:, d:d+1], max_lag=min(100, len(post_window)//2)
            )
            result[f'periodicity_post_{d}'] = float(period_post[0])
        else:
            result[f'periodicity_post_{d}'] = 0.0

        # 计算窗口期间的变化幅度相对于历史正常波动的比率
        if window_end > window_start:
            window_data = timeseries[window_start:window_end, d]
            window_mean = np.mean(window_data)
            window_std = np.std(window_data)

            # 历史基线
            if pre_window is not None and len(pre_window) > 0:
                baseline_mean = np.mean(pre_window[:, d])
                baseline_std = np.std(pre_window[:, d])

                # 变化幅度
                deviation = abs(window_mean - baseline_mean) / (baseline_std + 1e-8)
                result[f'deviation_{d}'] = float(deviation)
            else:
                result[f'deviation_{d}'] = 0.0

    return result
