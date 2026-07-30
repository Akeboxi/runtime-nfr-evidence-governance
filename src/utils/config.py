"""配置管理模块"""

from pathlib import Path
from typing import Any
from dataclasses import dataclass, field
import yaml


@dataclass
class ModelConfig:
    """模型配置"""
    hidden_dim: int = 128  # 128维隐藏层
    num_layers: int = 3    # 3层
    dropout: float = 0.2   # 0.2 dropout
    node_types: list[str] = field(default_factory=lambda: ["Vphy", "Vvm", "Vbiz"])


@dataclass
class FocalLossConfig:
    """Focal Loss 配置"""
    alpha: float = 0.25
    gamma: float = 2.0


@dataclass
class EarlyStoppingConfig:
    """早停配置"""
    patience: int = 10
    min_delta: float = 0.001


@dataclass
class TrainingConfig:
    """训练配置"""
    epochs: int = 100
    learning_rate: float = 0.01
    weight_decay: float = 1e-5
    batch_size: int = 1
    focal_loss: FocalLossConfig = field(default_factory=FocalLossConfig)
    optimizer: str = "adam"
    early_stopping: EarlyStoppingConfig = field(default_factory=EarlyStoppingConfig)


@dataclass
class RiskThresholdConfig:
    """风险阈值配置"""
    high: float = 0.8
    medium: float = 0.5


@dataclass
class LoessConfig:
    """LOESS 配置"""
    span: float = 0.3
    degree: int = 1


@dataclass
class EwmaConfig:
    """EWMA 配置"""
    alpha: float = 0.1


@dataclass
class DetectionConfig:
    """检测引擎配置"""
    loess: LoessConfig = field(default_factory=LoessConfig)
    ewma: EwmaConfig = field(default_factory=EwmaConfig)
    anomaly_threshold: float = 2.0


@dataclass
class DataConfig:
    """数据配置"""
    train_ratio: float = 0.8
    val_ratio: float = 0.1
    test_ratio: float = 0.1
    sequence_length: int = 60
    change_window_minutes: int = 30
    use_loess_residual: bool = False  # VM/业务节点是否使用LOESS残差作为输入


@dataclass
class PathConfig:
    """路径配置"""
    data_dir: Path = field(default_factory=lambda: Path("./data"))
    model_dir: Path = field(default_factory=lambda: Path("./models"))
    log_dir: Path = field(default_factory=lambda: Path("./logs"))


@dataclass
class Config:
    """全局配置类"""
    model: ModelConfig = field(default_factory=ModelConfig)
    training: TrainingConfig = field(default_factory=TrainingConfig)
    risk_threshold: RiskThresholdConfig = field(default_factory=RiskThresholdConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    data: DataConfig = field(default_factory=DataConfig)
    paths: PathConfig = field(default_factory=PathConfig)

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> "Config":
        """从 YAML 文件加载配置"""
        config_path = Path(config_path)
        if not config_path.exists():
            return cls()

        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> "Config":
        """从字典创建配置"""
        config = cls()

        if "model" in data:
            config.model = ModelConfig(**data["model"])
        if "training" in data:
            training_data = data["training"]
            if "focal_loss" in training_data:
                training_data["focal_loss"] = FocalLossConfig(**training_data["focal_loss"])
            if "early_stopping" in training_data:
                training_data["early_stopping"] = EarlyStoppingConfig(**training_data["early_stopping"])
            config.training = TrainingConfig(**training_data)
        if "risk_threshold" in data:
            config.risk_threshold = RiskThresholdConfig(**data["risk_threshold"])
        if "detection" in data:
            detection_data = data["detection"]
            if "loess" in detection_data:
                detection_data["loess"] = LoessConfig(**detection_data["loess"])
            if "ewma" in detection_data:
                detection_data["ewma"] = EwmaConfig(**detection_data["ewma"])
            config.detection = DetectionConfig(**detection_data)
        if "data" in data:
            config.data = DataConfig(**data["data"])
        if "paths" in data:
            paths_data = {k: Path(v) for k, v in data["paths"].items()}
            config.paths = PathConfig(**paths_data)

        return config

    def ensure_dirs(self) -> None:
        """确保所有必需的目录存在"""
        for path in [self.paths.data_dir, self.paths.model_dir, self.paths.log_dir]:
            path.mkdir(parents=True, exist_ok=True)


# 全局配置实例
_global_config: Config | None = None


def get_config(config_path: str | Path | None = None) -> Config:
    """获取全局配置实例"""
    global _global_config
    if _global_config is None:
        if config_path is None:
            config_path = Path(__file__).parent.parent.parent / "config.yaml"
        _global_config = Config.from_yaml(config_path)
    return _global_config


def reset_config() -> None:
    """重置全局配置"""
    global _global_config
    _global_config = None
