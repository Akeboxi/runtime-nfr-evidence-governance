"""损失函数模块 - Focal Loss实现"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Literal


class FocalLoss(nn.Module):
    """Focal Loss 损失函数

    用于解决样本不平衡问题，降低简单样本的权重，
    迫使模型专注于难以区分的样本。

    公式:
        FL(p_t) = -alpha_t * (1 - p_t)^gamma * log(p_t)

    其中:
        p_t: 预测概率（正样本为p，负样本为1-p）
        alpha: 平衡因子
        gamma: 聚焦参数
    """

    def __init__(
        self,
        alpha: float = 0.25,
        gamma: float = 2.0,
        reduction: Literal["none", "mean", "sum"] = "mean",
        label_smoothing: float = 0.0,
    ):
        """初始化Focal Loss

        Args:
            alpha: 平衡因子，用于平衡正负样本
            gamma: 聚焦参数，gamma越大，对易分类样本的降权越多
            reduction: 损失聚合方式
            label_smoothing: 标签平滑系数
        """
        super().__init__()
        self.alpha = alpha
        self.gamma = gamma
        self.reduction = reduction
        self.label_smoothing = label_smoothing

    def forward(
        self,
        inputs: torch.Tensor,
        targets: torch.Tensor,
    ) -> torch.Tensor:
        """计算Focal Loss

        Args:
            inputs: 模型预测值，shape (N,) 或 (N, 1)，经过sigmoid前的logits
            targets: 真实标签，shape (N,)，值为0或1

        Returns:
            损失值
        """
        if inputs.dim() > 1:
            inputs = inputs.squeeze(1)

        probs = torch.sigmoid(inputs)
        targets = targets.float()

        if self.label_smoothing > 0:
            targets = targets * (1 - self.label_smoothing) + 0.5 * self.label_smoothing

        bce_loss = F.binary_cross_entropy_with_logits(
            inputs, targets, reduction="none"
        )

        pt = targets * probs + (1 - targets) * (1 - probs)
        alpha_t = self.alpha * targets + (1 - self.alpha) * (1 - targets)
        focal_weight = alpha_t * (1 - pt).pow(self.gamma)

        loss = focal_weight * bce_loss

        if self.reduction == "mean":
            return loss.mean()
        elif self.reduction == "sum":
            return loss.sum()
        return loss


class WeightedBCELoss(nn.Module):
    """加权二元交叉熵损失

    作为Focal Loss的替代方案
    """

    def __init__(
        self,
        pos_weight: float | None = None,
        reduction: Literal["none", "mean", "sum"] = "mean",
    ):
        """初始化Weighted BCE Loss

        Args:
            pos_weight: 正样本权重
            reduction: 损失聚合方式
        """
        super().__init__()
        self.pos_weight = pos_weight
        self.reduction = reduction

    def forward(
        self,
        inputs: torch.Tensor,
        targets: torch.Tensor,
    ) -> torch.Tensor:
        """计算加权BCE Loss"""
        if inputs.dim() > 1:
            inputs = inputs.squeeze(1)

        weight = None
        if self.pos_weight is not None:
            weight = torch.ones_like(targets)
            weight[targets == 1] = self.pos_weight

        loss = F.binary_cross_entropy_with_logits(
            inputs,
            targets.float(),
            weight=weight,
            reduction=self.reduction,
        )

        return loss


class DiceLoss(nn.Module):
    """Dice Loss

    常用于分割任务，也可用于类别极度不平衡的分类问题
    """

    def __init__(
        self,
        smooth: float = 1.0,
        reduction: Literal["none", "mean", "sum"] = "mean",
    ):
        """初始化Dice Loss

        Args:
            smooth: 平滑系数，防止分母为0
            reduction: 损失聚合方式
        """
        super().__init__()
        self.smooth = smooth
        self.reduction = reduction

    def forward(
        self,
        inputs: torch.Tensor,
        targets: torch.Tensor,
    ) -> torch.Tensor:
        """计算Dice Loss"""
        if inputs.dim() > 1:
            inputs = inputs.squeeze(1)

        probs = torch.sigmoid(inputs)
        targets = targets.float()

        intersection = (probs * targets).sum(dim=-1)
        union = probs.sum(dim=-1) + targets.sum(dim=-1)

        dice = (2.0 * intersection + self.smooth) / (union + self.smooth)
        loss = 1.0 - dice

        if self.reduction == "mean":
            return loss.mean()
        elif self.reduction == "sum":
            return loss.sum()
        return loss


class CombinedLoss(nn.Module):
    """组合损失函数

    结合多种损失函数的优点
    """

    def __init__(
        self,
        focal_alpha: float = 0.25,
        focal_gamma: float = 2.0,
        focal_weight: float = 0.7,
        dice_weight: float = 0.3,
    ):
        """初始化组合损失

        Args:
            focal_alpha: Focal Loss的alpha参数
            focal_gamma: Focal Loss的gamma参数
            focal_weight: Focal Loss的权重
            dice_weight: Dice Loss的权重
        """
        super().__init__()
        self.focal_loss = FocalLoss(alpha=focal_alpha, gamma=focal_gamma)
        self.dice_loss = DiceLoss()
        self.focal_weight = focal_weight
        self.dice_weight = dice_weight

    def forward(
        self,
        inputs: torch.Tensor,
        targets: torch.Tensor,
    ) -> torch.Tensor:
        """计算组合损失"""
        focal = self.focal_loss(inputs, targets)
        dice = self.dice_loss(inputs, targets)
        return self.focal_weight * focal + self.dice_weight * dice


class TopologyAwareMarginLoss(nn.Module):
    """拓扑感知边际损失（Topology-Aware Margin, TAM）

    TAM是一种专门为图神经网络设计的损失函数，它关注样本在图拓扑中的位置分布。
    与Focal Loss不同，TAM根据节点的邻居标签分布来调整分类边际，
    使被大量正常邻居包围的风险节点获得更大的分类边际。

    公式:
        phi_ACM(i) = exp(-alpha * D_i)
        z_adj = z - delta * phi_ACM
        L_TAM = BCE(z_adj, y)

    其中:
        D_i: 节点i的邻居标签密度（风险类比例）
        alpha: ACM缩放参数
        delta: 边际调整幅度参数
    """

    def __init__(
        self,
        alpha: float = 1.0,
        delta: float = 0.5,
        reduction: Literal["none", "mean", "sum"] = "mean",
        use_focal: bool = True,
        focal_alpha: float = 0.25,
        focal_gamma: float = 2.0,
        pos_weight: float | None = None,
    ):
        """初始化TAM Loss

        Args:
            alpha: ACM指数衰减参数，越大则拓扑边际调整越激进
            delta: 边际调整幅度参数，控制整体调整力度
            reduction: 损失聚合方式
            use_focal: 是否结合Focal Loss
            focal_alpha: Focal Loss的alpha参数
            focal_gamma: Focal Loss的gamma参数
            pos_weight: 正样本权重，用于处理类别不平衡
        """
        super().__init__()
        self.alpha = alpha
        self.delta = delta
        self.reduction = reduction
        self.use_focal = use_focal
        self.focal_alpha = focal_alpha
        self.focal_gamma = focal_gamma
        self.pos_weight = pos_weight

    def _compute_neighbor_label_density(
        self,
        g: "dgl.DGLGraph",
        labels: torch.Tensor,
        node_indices: torch.Tensor,
    ) -> torch.Tensor:
        """计算邻居标签密度

        对于业务层节点，计算其L跳邻居中风险类的比例

        Args:
            g: DGL异构图
            labels: 节点标签 (N_biz,) 只需要业务节点标签
            node_indices: 需要计算的节点索引（本地索引，0 到 n_biz-1）

        Returns:
            D_i: 每个节点的邻居标签密度
        """
        device = labels.device
        n_biz = len(labels)

        # 获取图中的业务节点邻居关系
        adj = {i: [] for i in range(n_biz)}
        try:
            # 获取r_calling边
            if ("Vbiz", "r_calling", "Vbiz") in g.canonical_etypes:
                subg = g.edge_type_subgraph([("Vbiz", "r_calling", "Vbiz")])
                src, dst = subg.edges()
                # HeteroBatch 中，edge_type_subgraph 返回的 src/dst 是 Vbiz 类型的本地索引（0到n_biz-1）
                # 不需要减去偏移量，直接使用

                # 创建邻接表（本地索引）
                for s, d in zip(src.tolist(), dst.tolist()):
                    if 0 <= s < n_biz and 0 <= d < n_biz:
                        adj[s].append(d)
                        adj[d].append(s)  # 无向图
        except Exception:
            pass

        # 计算每个节点的邻居标签密度
        densities = []
        for idx in node_indices:
            idx_local = idx.item() if torch.is_tensor(idx) else idx

            # 如果 idx_local >= n_biz，说明它是全局索引，需要转换
            # 在 HeteroBatch 中，Vbiz 的全局索引 = idx_local - (n_phy + n_vm)
            # 但这里 node_indices 应该已经是本地索引
            if idx_local >= n_biz:
                # 尝试作为全局索引处理
                n_phy = g.num_nodes("Vphy")
                n_vm = g.num_nodes("Vvm")
                biz_offset = n_phy + n_vm
                idx_local = idx_local - biz_offset

            # 确保 idx_local 在有效范围内
            if 0 <= idx_local < n_biz:
                # 收集该节点及其邻居的标签
                neighbor_labels = [labels[idx_local].item()]
                for neighbor in adj.get(idx_local, []):
                    neighbor_labels.append(labels[neighbor].item())

                # 计算密度
                density = sum(neighbor_labels) / max(1, len(neighbor_labels))
                densities.append(density)
            else:
                # 节点不在图中或索引无效，密度为0
                densities.append(0.0)

        return torch.tensor(densities, device=device, dtype=torch.float)

    def forward(
        self,
        inputs: torch.Tensor,
        targets: torch.Tensor,
        g: "dgl.DGLGraph" = None,
        node_indices: torch.Tensor = None,
    ) -> torch.Tensor:
        """计算TAM Loss

        Args:
            inputs: 模型预测值，shape (N,) 或 (N, 1)，经过sigmoid前的logits
            targets: 真实标签，shape (N,)，值为0或1
            g: DGL图（可选，用于计算拓扑信息）
            node_indices: 节点索引（可选）

        Returns:
            损失值
        """
        if inputs.dim() > 1:
            inputs = inputs.squeeze(1)

        targets = targets.float()
        logits = inputs

        # 如果提供了图信息和节点索引，计算拓扑调整
        if g is not None and node_indices is not None:
            # 计算邻居标签密度
            D = self._compute_neighbor_label_density(g, targets, node_indices)

            # 计算ACM边际偏移量: phi = exp(-alpha * D)
            phi = torch.exp(-self.alpha * D)

            # 对于风险类（y=1），如果被大量正常节点包围（D小），phi会很大
            # 这会在logit上产生更大的负调整，迫使模型更关注这些"孤立"的风险节点
            phi_adjusted = phi * (targets * 2 - 1)  # 正类保留phi，负类取反

            # 调整logits: z_adj = z - delta * phi
            adjusted_logits = logits - self.delta * phi_adjusted
        else:
            # 如果没有图信息，退化为普通BCE或Focal Loss
            adjusted_logits = logits

        # 计算损失
        if self.use_focal:
            probs = torch.sigmoid(adjusted_logits)
            bce_loss = F.binary_cross_entropy_with_logits(
                adjusted_logits, targets, reduction="none"
            )

            pt = targets * probs + (1 - targets) * (1 - probs)
            alpha_t = self.focal_alpha * targets + (1 - self.focal_alpha) * (1 - targets)
            focal_weight = alpha_t * (1 - pt).pow(self.focal_gamma)

            loss = focal_weight * bce_loss
        else:
            loss = F.binary_cross_entropy_with_logits(adjusted_logits, targets, reduction="none")

        # 应用类权重（如果提供）
        if self.pos_weight is not None:
            weight = torch.ones_like(targets)
            weight[targets == 1] = self.pos_weight
            loss = loss * weight

        if self.reduction == "mean":
            return loss.mean()
        elif self.reduction == "sum":
            return loss.sum()
        return loss


class TAMFocalLoss(nn.Module):
    """TAM与Focal Loss的组合损失

    结合拓扑感知边际和Focal Loss的优点
    """

    def __init__(
        self,
        tam_alpha: float = 1.0,
        tam_delta: float = 0.5,
        focal_alpha: float = 0.25,
        focal_gamma: float = 2.0,
        tam_weight: float = 0.5,
        reduction: Literal["none", "mean", "sum"] = "mean",
    ):
        """初始化组合损失

        Args:
            tam_alpha: TAM的alpha参数
            tam_delta: TAM的delta参数
            focal_alpha: Focal Loss的alpha参数
            focal_gamma: Focal Loss的gamma参数
            tam_weight: TAM损失的权重
            reduction: 损失聚合方式
        """
        super().__init__()
        self.tam_loss = TopologyAwareMarginLoss(
            alpha=tam_alpha,
            delta=tam_delta,
            reduction="none",
            use_focal=False,
        )
        self.focal_loss = FocalLoss(
            alpha=focal_alpha,
            gamma=focal_gamma,
            reduction="none",
        )
        self.tam_weight = tam_weight
        self.focal_weight = 1.0 - tam_weight
        self.reduction = reduction

    def forward(
        self,
        inputs: torch.Tensor,
        targets: torch.Tensor,
        g: "dgl.DGLGraph" = None,
        node_indices: torch.Tensor = None,
    ) -> torch.Tensor:
        """计算组合损失"""
        tam = self.tam_loss(inputs, targets, g, node_indices)
        focal = self.focal_loss(inputs, targets)

        loss = self.tam_weight * tam + self.focal_weight * focal

        if self.reduction == "mean":
            return loss.mean()
        elif self.reduction == "sum":
            return loss.sum()
        return loss


class TACMLoss(nn.Module):
    """拓扑感知连续边际损失 (Topology-Aware Continuous Margin Loss, TACM)

    基于软标签分布计算动态拓扑边际，专门为连续软标签拟合设计。

    核心公式:
    1. 拓扑风险密度计算:
       y_filtered = y_soft^kappa
       D_i = (sum_{v in N(i) U {i}} y_filtered_v) / (d_i + 1)

    2. 自适应边际计算:
       delta_i = y_soft * alpha * exp(-beta * D_i)
       delta_i = delta_i.detach()  # 阻断梯度回传

    3. 带边际的 Soft BCE:
       p_pos = sigmoid(logits - delta_i)
       p_neg = 1 - sigmoid(logits)
       loss = -mean(y_soft * log(p_pos) + (1 - y_soft) * log(p_neg))
    """

    def __init__(
        self,
        kappa: float = 3.0,
        alpha: float = 2.0,
        beta: float = 5.0,
        reduction: Literal["none", "mean", "sum"] = "mean",
    ):
        """初始化 TACM Loss

        Args:
            kappa: 非线性滤镜指数，用于放大高风险标签的影响
            alpha: 边际缩放因子
            beta: 密度衰减因子
            reduction: 损失聚合方式
        """
        super().__init__()
        self.kappa = kappa
        self.alpha = alpha
        self.beta = beta
        self.reduction = reduction

    def _compute_density(
        self,
        y_soft: torch.Tensor,
        edge_index: torch.Tensor,
        num_nodes: int,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Compute local density and filtered labels for a Biz-only edge view."""
        y_filtered = torch.pow(y_soft, self.kappa)

        if edge_index.numel() == 0:
            degree = torch.zeros(num_nodes, device=y_soft.device, dtype=torch.float)
            density = y_filtered.clone()
            return density, y_filtered

        row, col = edge_index[0], edge_index[1]

        degree = torch.zeros(num_nodes, device=y_soft.device, dtype=torch.float)
        degree.scatter_add_(0, col, torch.ones_like(col, dtype=torch.float))

        density = torch.zeros(num_nodes, device=y_soft.device, dtype=torch.float)
        density.scatter_add_(0, col, y_filtered[row])
        density = density + y_filtered
        density = density / (degree + 1.0)
        return density, y_filtered

    def _compute_margin(
        self,
        y_soft: torch.Tensor,
        density: torch.Tensor,
    ) -> torch.Tensor:
        """Compute the detached TACM margin from local density."""
        delta = y_soft * self.alpha * torch.exp(-self.beta * density)
        return delta.detach()

    def forward(
        self,
        logits: torch.Tensor,
        y_soft: torch.Tensor,
        edge_index: torch.Tensor,
        num_nodes: int,
    ) -> torch.Tensor:
        """计算 TACM Loss

        Args:
            logits: 模型预测值，shape (N,) 或 (N, 1)，未经 sigmoid 的原始 logits
            y_soft: 软标签，shape (N,)，值为 [0, 1] 连续的 impact_score
            edge_index: 边索引，shape (2, E)，包含 r_calling 和 r_deployment
            num_nodes: 业务节点总数

        Returns:
            损失值
        """
        if logits.dim() > 1:
            logits = logits.squeeze(-1)

        y_soft = y_soft.float()

        # 1. 拓扑风险密度计算
        D, _ = self._compute_density(y_soft, edge_index, num_nodes)

        # 2. 自适应边际计算
        delta = self._compute_margin(y_soft, D)

        # 3. 带边际惩罚的预测概率
        p_pos = torch.sigmoid(logits - delta)
        p_neg = 1.0 - torch.sigmoid(logits)

        # 4. Soft BCE Loss
        # log-sum-exp 稳定版本
        loss = -(
            y_soft * torch.log(p_pos + 1e-7) +
            (1.0 - y_soft) * torch.log(p_neg + 1e-7)
        )

        if self.reduction == "mean":
            return loss.mean()
        elif self.reduction == "sum":
            return loss.sum()
        return loss


class TACMMaelLoss(nn.Module):
    """拓扑感知连续边际 + MAE 混合损失

    结合 TACM Loss 的拓扑感知能力和直接 MAE 损失对软标签的精确拟合能力。

    公式:
        L_total = (1 - mae_weight) * TACM + mae_weight * MAE

    MAE 损失直接惩罚预测值与软标签的绝对误差，有助于拟合极端值。
    """

    def __init__(
        self,
        kappa: float = 3.0,
        alpha: float = 2.0,
        beta: float = 5.0,
        mae_weight: float = 0.5,
        reduction: Literal["none", "mean", "sum"] = "mean",
    ):
        """初始化混合损失

        Args:
            kappa: 非线性滤镜指数
            alpha: 边际缩放因子
            beta: 密度衰减因子
            mae_weight: MAE 损失的权重 (0~1)
            reduction: 损失聚合方式
        """
        super().__init__()
        self.tacm = TACMLoss(kappa=kappa, alpha=alpha, beta=beta, reduction="none")
        self.mae_weight = mae_weight
        self.reduction = reduction

    def forward(
        self,
        logits: torch.Tensor,
        y_soft: torch.Tensor,
        edge_index: torch.Tensor,
        num_nodes: int,
    ) -> torch.Tensor:
        """计算 TACM + MAE 混合损失"""
        # TACM Loss (per-sample)
        tacm_loss = self.tacm(logits, y_soft, edge_index, num_nodes)

        # 直接 MAE Loss (使用 sigmoid 将 logits 转为概率)
        if logits.dim() > 1:
            logits = logits.squeeze(-1)
        probs = torch.sigmoid(logits)
        mae_loss = torch.abs(probs - y_soft)

        # 加权组合
        combined = (1 - self.mae_weight) * tacm_loss + self.mae_weight * mae_loss

        if self.reduction == "mean":
            return combined.mean()
        elif self.reduction == "sum":
            return combined.sum()
        return combined


class TACMMseLoss(nn.Module):
    """拓扑感知连续边际 + MSE 混合损失

    MSE 损失对大误差惩罚更重，有助于拟合极端值。
    """

    def __init__(
        self,
        kappa: float = 3.0,
        alpha: float = 2.0,
        beta: float = 5.0,
        mse_weight: float = 0.7,
        reduction: Literal["none", "mean", "sum"] = "mean",
    ):
        """初始化混合损失

        Args:
            kappa: 非线性滤镜指数
            alpha: 边际缩放因子
            beta: 密度衰减因子
            mse_weight: MSE 损失的权重 (0~1)
            reduction: 损失聚合方式
        """
        super().__init__()
        self.tacm = TACMLoss(kappa=kappa, alpha=alpha, beta=beta, reduction="none")
        self.mse_weight = mse_weight
        self.reduction = reduction

    def forward(
        self,
        logits: torch.Tensor,
        y_soft: torch.Tensor,
        edge_index: torch.Tensor,
        num_nodes: int,
    ) -> torch.Tensor:
        """计算 TACM + MSE 混合损失"""
        # TACM Loss (per-sample)
        tacm_loss = self.tacm(logits, y_soft, edge_index, num_nodes)

        # 直接 MSE Loss (使用 sigmoid 将 logits 转为概率)
        if logits.dim() > 1:
            logits = logits.squeeze(-1)
        probs = torch.sigmoid(logits)
        mse_loss = torch.pow(probs - y_soft, 2)

        # 加权组合
        combined = (1 - self.mse_weight) * tacm_loss + self.mse_weight * mse_loss

        if self.reduction == "mean":
            return combined.mean()
        elif self.reduction == "sum":
            return combined.sum()
        return combined


class TACMABVDLoss(nn.Module):
    """TACM with Asymmetric Bi-View Density (ABVD).

    We keep the original TACM continuous-margin backbone, then modulate the
    margin with two directional Biz-level density views:
    - upstream density D_up: peer Biz nodes that share the same VM deployment
    - downstream density D_down: Biz calling neighbors

    Directional weighting follows the plan doc conservatively:
    - upstream uses exp(-beta_up * D_up): sparse upstream support gets larger margin
    - downstream uses sigmoid(beta_down * (D_down - 0.5)): dense downstream spread
      increases the directional weight smoothly instead of sharply.
    """

    def __init__(
        self,
        kappa: float = 3.0,
        alpha: float = 2.0,
        beta: float = 5.0,
        beta_up: float = 3.0,
        beta_down: float = 4.0,
        reduction: Literal["none", "mean", "sum"] = "mean",
    ):
        super().__init__()
        self.tacm = TACMLoss(kappa=kappa, alpha=alpha, beta=beta, reduction="none")
        self.beta_up = beta_up
        self.beta_down = beta_down
        self.reduction = reduction

    def forward(
        self,
        logits: torch.Tensor,
        y_soft: torch.Tensor,
        calling_edge_index: torch.Tensor,
        upstream_edge_index: torch.Tensor,
        num_nodes: int,
    ) -> torch.Tensor:
        if logits.dim() > 1:
            logits = logits.squeeze(-1)

        y_soft = y_soft.float()
        d_base, _ = self.tacm._compute_density(y_soft, calling_edge_index, num_nodes)
        d_up, _ = self.tacm._compute_density(y_soft, upstream_edge_index, num_nodes)
        d_down = d_base

        base_margin = self.tacm._compute_margin(y_soft, d_base)
        upstream_weight = torch.exp(-self.beta_up * d_up)
        downstream_weight = torch.sigmoid(self.beta_down * (d_down - 0.5))
        directional_weight = 0.5 * (upstream_weight + downstream_weight)
        delta = (base_margin * directional_weight).detach()

        p_pos = torch.sigmoid(logits - delta)
        p_neg = 1.0 - torch.sigmoid(logits)
        loss = -(
            y_soft * torch.log(p_pos + 1e-7) +
            (1.0 - y_soft) * torch.log(p_neg + 1e-7)
        )

        if self.reduction == "mean":
            return loss.mean()
        elif self.reduction == "sum":
            return loss.sum()
        return loss

class TACMSCRLoss(nn.Module):
    """Topology-Aware Continuous Margin + Subgraph Consistency Regularization.

    L_total = L_TACM_full + lambda_scr * L_SCR

    L_SCR = MSE(prob_full, prob_sub) where prob is sigmoid(logit).
    Encourages model to make similar predictions on the full graph
    (all 5 edge types) and the subgraph (no r_calling), so online
    inference at deploy time (3 edge types) remains consistent.
    """

    def __init__(
        self,
        kappa: float = 3.0,
        alpha: float = 2.0,
        beta: float = 5.0,
        lambda_scr: float = 0.5,
        reduction: Literal["none", "mean", "sum"] = "mean",
    ):
        super().__init__()
        self.tacm = TACMLoss(kappa=kappa, alpha=alpha, beta=beta, reduction="none")
        self.lambda_scr = lambda_scr
        self.reduction = reduction

    def forward(
        self,
        logits_full: torch.Tensor,
        logits_sub: torch.Tensor,
        y_soft: torch.Tensor,
        edge_index: torch.Tensor,
        num_nodes: int,
    ) -> torch.Tensor:
        tacm_loss = self.tacm(logits_full, y_soft, edge_index, num_nodes)
        # Keep a per-node SCR term so reduction="none" remains meaningful.
        p_full = torch.sigmoid(logits_full).squeeze(-1)
        p_sub = torch.sigmoid(logits_sub).squeeze(-1)
        scr_loss = (p_full - p_sub).pow(2)
        combined = tacm_loss + self.lambda_scr * scr_loss
        if self.reduction == "mean":
            return combined.mean()
        elif self.reduction == "sum":
            return combined.sum()
        return combined


class TACMSCRABVDLoss(nn.Module):
    """TACM+ABVD with Subgraph Consistency Regularization.

    L_total = L_TACMABVD_full + lambda_scr * L_SCR

    The directional ABVD margin is computed on the full graph, while SCR keeps
    the full-graph and subgraph predictions aligned at probability level.
    """

    def __init__(
        self,
        kappa: float = 3.0,
        alpha: float = 2.0,
        beta: float = 5.0,
        beta_up: float = 3.0,
        beta_down: float = 4.0,
        lambda_scr: float = 0.5,
        reduction: Literal["none", "mean", "sum"] = "mean",
    ):
        super().__init__()
        self.tacm_abvd = TACMABVDLoss(
            kappa=kappa,
            alpha=alpha,
            beta=beta,
            beta_up=beta_up,
            beta_down=beta_down,
            reduction="none",
        )
        self.lambda_scr = lambda_scr
        self.reduction = reduction

    def forward(
        self,
        logits_full: torch.Tensor,
        logits_sub: torch.Tensor,
        y_soft: torch.Tensor,
        calling_edge_index: torch.Tensor,
        upstream_edge_index: torch.Tensor,
        num_nodes: int,
    ) -> torch.Tensor:
        abvd_loss = self.tacm_abvd(
            logits_full,
            y_soft,
            calling_edge_index,
            upstream_edge_index,
            num_nodes,
        )
        p_full = torch.sigmoid(logits_full).squeeze(-1)
        p_sub = torch.sigmoid(logits_sub).squeeze(-1)
        scr_loss = (p_full - p_sub).pow(2)
        combined = abvd_loss + self.lambda_scr * scr_loss
        if self.reduction == "mean":
            return combined.mean()
        elif self.reduction == "sum":
            return combined.sum()
        return combined
