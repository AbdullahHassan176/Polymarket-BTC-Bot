# ML Improvements from Ethereum/Bitcoin Price Prediction Research

Based on *"Ethereum Price Prediction Using Machine Learning Techniques – A Comparative Study"* (IJEAST 2022) and cited papers.

## Key Findings from the Paper

| Model | 30-day MAPE | 90-day MAPE | Best for |
|-------|-------------|-------------|----------|
| Simple RNN | 40.46% | 46.76% | — |
| LSTM | 30.02% | 27.38% | Good |
| **Bi-LSTM** | **7.26%** | **9.57%** | **Best** |

- **Short-term prediction > long-term**: 30-day errors lower than 90-day.
- **Bi-LSTM** captures bidirectional context and outperforms LSTM/RNN.
- **Closing price** as primary feature; min-max scaling [0,1].
- Related work: GRU ~71% accuracy; CNN+LSTM for crypto; cross-asset (ETH, Zcash) improves BTC prediction.

## Our Bot vs. The Research

| Aspect | Paper (Ethereum) | Our Bot (BTC 5-min) |
|--------|------------------|---------------------|
| Horizon | 30–90 days | **5 minutes** (much shorter) |
| Task | Price level prediction | **Direction** (UP/DOWN binary) |
| Model | B-S digital (vol-based) | Could add LSTM/Bi-LSTM |
| Features | Closing price only | OHLC, EMA, ATR, IBS |

## Proposed Improvements

### 1. LSTM/Bi-LSTM Direction Classifier (High Impact)

**Idea**: Train a model to predict P(UP) for the next 5-min window from recent 1m candle sequences.

- **Input**: Rolling window of N 1m candles (e.g. 20–60) with OHLCV + indicators (EMA, ATR%, IBS).
- **Output**: P(UP) or binary UP/DOWN.
- **Training**: Historical 5-min windows with labeled outcome (resolved YES/NO).
- **Integration**: Replace or blend with `model_implied_p_up()` in `btc_5m_fair_value.py`.

**Data needed**: Backfill 5-min resolution outcomes from OKX or Polymarket history.

### 2. Richer Feature Set (Medium Impact)

The paper used closing price only. Related work suggests:

- **OHLC** (open, high, low, close) – paper [2] found OHLC improves over close-only.
- **Volume** – we have it; add as normalized feature.
- **ATR%, IBS** – we already compute these; feed to ML model.
- **EMA spread** – momentum signal.

### 3. Min-Max Normalization (Low Effort)

Paper used min-max scaler on closing price. For sequences, normalize each feature to [0,1] over rolling window to reduce scale sensitivity.

### 4. Cross-Asset Features (Future)

Paper [10]: Bitcoin prediction improved using ETH, Zcash, Litecoin. We could add ETH 1m close as an auxiliary feature if we fetch it.

### 5. Hybrid Model: B-S + ML

- Keep B-S digital as **baseline** when volatility is reliable.
- Add **ML override**: when LSTM P(UP) disagrees strongly with B-S (e.g. |LSTM - B-S| > 0.15), use LSTM or blend.
- Or use ML only for **contrarian filter**: `model_ok_yes = ml_p_up >= yes_price + edge`.

## Implementation Status

| Phase | Task | Status |
|-------|------|--------|
| 1 | Backfill from OKX history | `python -m ml.backfill_training_data --windows 600` |
| 2 | sklearn RF with ETH feature | `python -m ml.train_direction_model --backfill logs/ml_training_backfill.csv` |
| 3 | Bi-LSTM (PyTorch) | `python -m ml.train_lstm_model --candles 500` |
| 4 | Model integration | `MODEL_USE_ML=True` in config; btc_5m_fair_value.py |

## ml/ Module

- **direction_model.py**: sklearn `ml_p_up()` with optional `eth_close_pct_change`
- **backfill_training_data.py**: OKX history → labeled 5-min windows (with ETH)
- **lstm_model.py**: Bi-LSTM `lstm_p_up(sequence)` for sequence input
- **train_direction_model.py**: Train RF, merge backfill + trade data
- **train_lstm_model.py**: Train Bi-LSTM from candle sequences
