"""
ml/lstm_model.py – Bi-LSTM for 5-min direction prediction.

Research: Bi-LSTM outperforms LSTM and RNN for crypto price prediction.
Input: sequences of [open, high, low, close, vol_norm, atr_pct, ibs] per 1m candle.
Output: P(UP) for the 5-min window.

Requires: pip install torch
"""

from pathlib import Path
from typing import List, Optional

import numpy as np


def _get_model_path() -> Path:
    return Path(__file__).parent / "lstm_model.pt"


def _get_scaler_path() -> Path:
    return Path(__file__).parent / "lstm_scaler.npz"


def lstm_p_up(sequence: np.ndarray, use_bidirectional: bool = True) -> float:
    """
    Predict P(UP) from a sequence of 1m candle features.

    sequence: shape (seq_len, n_features). Features: [open_norm, high_norm, low_norm, close_norm, vol_norm, atr_pct, ibs]
    Returns 0.5 if model unavailable.
    """
    try:
        import torch

        path = _get_model_path()
        if not path.exists():
            return 0.5

        model, scaler_mean, scaler_std = _load_lstm_model(path)
        if model is None:
            return 0.5

        X = np.asarray(sequence, dtype=np.float32)
        if scaler_mean is not None and scaler_std is not None:
            X = (X - scaler_mean) / (scaler_std + 1e-8)
        X = torch.from_numpy(X).unsqueeze(0)  # (1, seq_len, n_features)

        model.eval()
        with torch.no_grad():
            logits = model(X)
            proba = torch.sigmoid(logits).item()
        return float(proba)
    except Exception:
        return 0.5


def _load_lstm_model(path: Path):
    """Load model and scaler. Returns (model, scaler_mean, scaler_std) or (None, None, None)."""
    try:
        import torch

        ckpt = torch.load(path, map_location="cpu")
        n_features = ckpt.get("n_features", 7)
        hidden = ckpt.get("hidden", 32)
        n_layers = ckpt.get("n_layers", 2)
        bidirectional = ckpt.get("bidirectional", True)

        model = _BiLSTMClassifier(
            n_features=n_features,
            hidden=hidden,
            n_layers=n_layers,
            bidirectional=bidirectional,
        )
        model.load_state_dict(ckpt["state_dict"])
        model.eval()

        scaler_path = _get_scaler_path()
        scaler_mean = scaler_std = None
        if scaler_path.exists():
            data = np.load(scaler_path)
            scaler_mean = data["mean"]
            scaler_std = data["std"]
        return model, scaler_mean, scaler_std
    except Exception:
        return None, None, None


def build_sequences_from_candles(
    df,
    seq_len: int = 20,
    window_minutes: int = 5,
) -> tuple:
    """
    Build (X, y) from candle DataFrame with indicator columns.
    X: (n_samples, seq_len, n_features), y: (n_samples,)
    """
    if df.empty or len(df) < seq_len + window_minutes + 35:
        return np.zeros((0, seq_len, 7)), np.zeros(0)

    df = df.copy()
    if "atr_pct" not in df.columns:
        df["atr14"] = (df["high"] - df["low"]).rolling(14).mean()
        df["atr_pct"] = df["atr14"] / df["close"]
    df["ibs"] = np.where(
        (df["high"] - df["low"]) > 0,
        (df["close"] - df["low"]) / (df["high"] - df["low"]),
        0.5,
    )

    # Normalize OHLC by close
    c = df["close"].values
    df["open_norm"] = df["open"].values / (c + 1e-8)
    df["high_norm"] = df["high"].values / (c + 1e-8)
    df["low_norm"] = df["low"].values / (c + 1e-8)
    df["close_norm"] = 1.0
    vol = df["vol"].rolling(20).mean().bfill()
    df["vol_norm"] = (df["vol"] / (vol + 1)).values

    feats = ["open_norm", "high_norm", "low_norm", "close_norm", "vol_norm", "atr_pct", "ibs"]
    df = df.dropna(subset=feats)

    X_list, y_list = [], []
    for i in range(len(df) - seq_len - window_minutes):
        seq = df[feats].iloc[i : i + seq_len].values.astype(np.float32)
        # Label: next window_minutes candles - did close go up?
        window = df.iloc[i + seq_len : i + seq_len + window_minutes]
        if len(window) < window_minutes:
            continue
        first_open = window.iloc[0]["open"]
        last_close = window.iloc[-1]["close"]
        label = 1 if last_close >= first_open else 0
        X_list.append(seq)
        y_list.append(label)

    if not X_list:
        return np.zeros((0, seq_len, 7)), np.zeros(0)
    X = np.stack(X_list)
    y = np.array(y_list, dtype=np.float32)
    return X, y


def train_lstm(
    X: np.ndarray,
    y: np.ndarray,
    epochs: int = 50,
    lr: float = 0.001,
    bidirectional: bool = True,
) -> dict:
    """Train Bi-LSTM and save. Returns metrics dict."""
    import torch

    if len(X) < 50:
        return {"error": "need 50+ samples"}

    # Normalize
    scaler_mean = X.reshape(-1, X.shape[-1]).mean(axis=0)
    scaler_std = X.reshape(-1, X.shape[-1]).std(axis=0) + 1e-8
    X_norm = (X - scaler_mean) / scaler_std

    X_t = torch.from_numpy(X_norm.astype(np.float32))
    y_t = torch.from_numpy(y.reshape(-1, 1).astype(np.float32))

    model = _BiLSTMClassifier(
        n_features=X.shape[2],
        hidden=32,
        n_layers=2,
        bidirectional=bidirectional,
    )
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = torch.nn.BCEWithLogitsLoss()

    model.train()
    for ep in range(epochs):
        opt.zero_grad()
        logits = model(X_t).unsqueeze(1)
        loss = loss_fn(logits, y_t)
        loss.backward()
        opt.step()

    model.eval()
    with torch.no_grad():
        proba = torch.sigmoid(model(X_t)).numpy().ravel()
        pred = (proba >= 0.5).astype(int)
        acc = (pred == y.astype(int)).mean()

    out_dir = Path(__file__).parent
    torch.save({
        "state_dict": model.state_dict(),
        "n_features": X.shape[2],
        "hidden": 32,
        "n_layers": 2,
        "bidirectional": bidirectional,
    }, out_dir / "lstm_model.pt")
    np.savez(out_dir / "lstm_scaler.npz", mean=scaler_mean, std=scaler_std)
    return {"accuracy": float(acc), "samples": len(X)}


try:
    import torch

    class _BiLSTMClassifier(torch.nn.Module):
        def __init__(self, n_features: int = 7, hidden: int = 32, n_layers: int = 2, bidirectional: bool = True):
            super().__init__()
            self.n_features = n_features
            self.hidden = hidden
            self.n_layers = n_layers
            self.bidirectional = bidirectional
            self.lstm = torch.nn.LSTM(
                n_features, hidden, num_layers=n_layers, batch_first=True,
                bidirectional=bidirectional, dropout=0.1,
            )
            mult = 2 if bidirectional else 1
            self.fc = torch.nn.Linear(hidden * mult, 1)

        def forward(self, x):
            out, (h_n, _) = self.lstm(x)
            if self.bidirectional:
                h = torch.cat((h_n[-2], h_n[-1]), dim=1)
            else:
                h = h_n[-1]
            return self.fc(h).squeeze(-1)
except ImportError:
    pass
