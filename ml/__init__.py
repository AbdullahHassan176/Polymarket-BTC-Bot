"""
ml/ – Machine learning models for 5-min direction prediction.

Based on research: Bi-LSTM > LSTM > RNN for crypto price prediction.
This module provides a sklearn-based quick win and structure for future LSTM.

See docs/ML_IMPROVEMENTS_FROM_RESEARCH.md for full roadmap.
"""

from ml.direction_model import ml_p_up, build_training_data

__all__ = ["ml_p_up", "build_training_data"]
