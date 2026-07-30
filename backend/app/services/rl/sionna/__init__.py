"""
Sionna GPU Channel Map Pipeline.

Modules:
  channel_precompute  — Offline A100 ray tracing → .npy SINR maps
  channel_lookup      — Runtime bilinear interpolation from .npy
"""
from .channel_lookup import ChannelLookup, load_channel_lookup
__all__ = ["ChannelLookup", "load_channel_lookup"]
