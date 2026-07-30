"""
Federated RL for Universal V2X Policy.

Architecture:
  • 50 virtual FL clients, each representing one Korean region
  • FedAvg aggregation on GNN policy weights (McMahan et al. 2017)
  • Differential privacy: Gaussian noise σ=0.01 on client gradients

Modules:
  fl_server  — Flower server with FedAvg strategy
  fl_client  — Flower client (one per region)
"""
from .fl_server import start_fl_server
from .fl_client import V2XFLClient
__all__ = ["start_fl_server", "V2XFLClient"]
