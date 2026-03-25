"""
Direct on-chain CTF redemption via Gnosis Safe (v1.3.0).

Flow:
  1. Encode CTF calldata (redeemPositions for all markets)
  2. Build EIP-712 Safe transaction hash (v1.3.0)
  3. Sign with the EOA private key that owns the Safe
  4. Call Safe.execTransaction — EOA pays gas

For NegRisk binary markets (Polymarket 5-min Up/Down):
  - Try CTF.redeemPositions directly (binary neg_risk conditions are standard CTF)
  - Fallback: NegRiskAdapter.convertAndRedeemPositions with CTF approval step

No Parsec, no polymarket-apis. Same approach as Polymarket-Bitcoin-Oracle-Latency-Arbitrage-Bot.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

from eth_account import Account
from web3 import Web3
from web3.types import TxReceipt

logger = logging.getLogger(__name__)

POLYGON_RPC   = "https://polygon-bor-rpc.publicnode.com"
CTF_ADDR      = Web3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")
USDC_ADDR     = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
NEG_RISK_ADDR = Web3.to_checksum_address("0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296")
ZERO_BYTES32  = b"\x00" * 32

CTF_ABI = [
    {"name": "redeemPositions", "type": "function", "inputs": [
        {"name": "collateralToken", "type": "address"},
        {"name": "parentCollectionId", "type": "bytes32"},
        {"name": "conditionId", "type": "bytes32"},
        {"name": "indexSets", "type": "uint256[]"},
    ], "outputs": [], "stateMutability": "nonpayable"},
    # ERC-1155 helpers for reading balances
    {"name": "balanceOf", "type": "function", "inputs": [
        {"name": "account", "type": "address"},
        {"name": "id", "type": "uint256"},
    ], "outputs": [{"type": "uint256"}], "stateMutability": "view"},
    {"name": "isApprovedForAll", "type": "function", "inputs": [
        {"name": "account", "type": "address"},
        {"name": "operator", "type": "address"},
    ], "outputs": [{"type": "bool"}], "stateMutability": "view"},
    {"name": "setApprovalForAll", "type": "function", "inputs": [
        {"name": "operator", "type": "address"},
        {"name": "approved", "type": "bool"},
    ], "outputs": [], "stateMutability": "nonpayable"},
]
NEG_RISK_ABI = [
    {"name": "convertAndRedeemPositions", "type": "function", "inputs": [
        {"name": "conditionId", "type": "bytes32"},
        {"name": "amounts", "type": "uint256[]"},
    ], "outputs": [], "stateMutability": "nonpayable"},
]
SAFE_ABI = [
    {"name": "execTransaction", "type": "function", "inputs": [
        {"name": "to", "type": "address"}, {"name": "value", "type": "uint256"},
        {"name": "data", "type": "bytes"}, {"name": "operation", "type": "uint8"},
        {"name": "safeTxGas", "type": "uint256"}, {"name": "baseGas", "type": "uint256"},
        {"name": "gasPrice", "type": "uint256"}, {"name": "gasToken", "type": "address"},
        {"name": "refundReceiver", "type": "address"}, {"name": "signatures", "type": "bytes"},
    ], "outputs": [{"type": "bool"}], "stateMutability": "payable"},
    {"name": "nonce", "type": "function", "inputs": [], "outputs": [{"type": "uint256"}], "stateMutability": "view"},
    {"name": "getTransactionHash", "type": "function", "inputs": [
        {"name": "to", "type": "address"}, {"name": "value", "type": "uint256"},
        {"name": "data", "type": "bytes"}, {"name": "operation", "type": "uint8"},
        {"name": "safeTxGas", "type": "uint256"}, {"name": "baseGas", "type": "uint256"},
        {"name": "gasPrice", "type": "uint256"}, {"name": "gasToken", "type": "address"},
        {"name": "refundReceiver", "type": "address"}, {"name": "_nonce", "type": "uint256"},
    ], "outputs": [{"type": "bytes32"}], "stateMutability": "view"},
]
CHAIN_ID = 137


def _safe_sign(account, safe_addr: str, target: str, data_bytes: bytes, nonce: int) -> bytes:
    """
    Sign a Gnosis Safe v1.3.0 transaction using EIP-712 typed data.
    eth_account >= 0.9 API: pass domain_data / message_types / message_data separately.
    More reliable than getTransactionHash + unsafe_sign_hash because it avoids
    any hex-string vs bytes ABI-encoding mismatch between calls.
    """
    zero_addr = "0x0000000000000000000000000000000000000000"
    domain_data = {
        "chainId": CHAIN_ID,
        "verifyingContract": safe_addr,
    }
    message_types = {
        "SafeTx": [
            {"name": "to",              "type": "address"},
            {"name": "value",           "type": "uint256"},
            {"name": "data",            "type": "bytes"},
            {"name": "operation",       "type": "uint8"},
            {"name": "safeTxGas",       "type": "uint256"},
            {"name": "baseGas",         "type": "uint256"},
            {"name": "gasPrice",        "type": "uint256"},
            {"name": "gasToken",        "type": "address"},
            {"name": "refundReceiver",  "type": "address"},
            {"name": "nonce",           "type": "uint256"},
        ],
    }
    message_data = {
        "to":              target,
        "value":           0,
        "data":            data_bytes,
        "operation":       0,
        "safeTxGas":       0,
        "baseGas":         0,
        "gasPrice":        0,
        "gasToken":        zero_addr,
        "refundReceiver":  zero_addr,
        "nonce":           nonce,
    }
    signed = account.sign_typed_data(
        domain_data=domain_data,
        message_types=message_types,
        message_data=message_data,
    )
    v = signed.v
    if v < 27:
        v += 27
    return signed.r.to_bytes(32, "big") + signed.s.to_bytes(32, "big") + bytes([v])


def _exec_safe_tx(
    w3: Web3,
    safe,
    account,
    target: str,
    data_bytes: bytes,
    label: str = "",
    safe_addr: Optional[str] = None,
) -> Optional[str]:
    """Build, sign (EIP-712), and submit one Gnosis Safe execTransaction. Returns tx hash or None."""
    zero = "0x0000000000000000000000000000000000000000"
    _safe_addr = safe_addr or ""  # caller must pass safe_addr for EIP-712

    try:
        nonce = safe.functions.nonce().call()
    except Exception as ex:
        logger.error("[REDEEM] Could not fetch Safe nonce: %s", ex)
        return None

    sig = _safe_sign(account, _safe_addr, target, data_bytes, nonce)

    try:
        gas_est = safe.functions.execTransaction(
            target, 0, data_bytes, 0, 0, 0, 0, zero, zero, sig,
        ).estimate_gas({"from": account.address})
        gas_limit = int(gas_est * 1.3)
    except Exception as ex:
        err_str = str(ex)
        if "GS013" in err_str:
            logger.warning("[REDEEM] %s inner call reverted (GS013) — skipping tx", label)
        else:
            logger.warning("[REDEEM] %s gas estimate failed — skipping: %s", label, err_str)
        # Do NOT submit a transaction we know will revert — it wastes POL.
        return None

    gas_price = w3.eth.gas_price
    tx = safe.functions.execTransaction(
        target, 0, data_bytes, 0, 0, 0, 0, zero, zero, sig,
    ).build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "gas": gas_limit,
        "gasPrice": gas_price,
        "chainId": CHAIN_ID,
    })

    try:
        signed_tx = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        receipt: TxReceipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt["status"] == 1:
            return tx_hash.hex()
        logger.error("[REDEEM] %s transaction reverted: %s", label, tx_hash.hex())
        return None
    except Exception as ex:
        logger.warning("[REDEEM] %s send failed, retrying: %s", label, ex)
        time.sleep(4)
        try:
            gas_price_retry = int(w3.eth.gas_price * 1.2)
            tx_retry = safe.functions.execTransaction(
                target, 0, data_bytes, 0, 0, 0, 0, zero, zero, sig,
            ).build_transaction({
                "from": account.address,
                "nonce": w3.eth.get_transaction_count(account.address),
                "gas": gas_limit,
                "gasPrice": gas_price_retry,
                "chainId": CHAIN_ID,
            })
            signed_tx = account.sign_transaction(tx_retry)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            return tx_hash.hex() if receipt["status"] == 1 else None
        except Exception as ex2:
            logger.error("[REDEEM] %s retry failed: %s", label, ex2)
            return None


def redeem_winning_position(
    condition_id: str,
    direction: str,
    neg_risk: bool = True,
    size_matched: float = 0.0,
    safe_addr: Optional[str] = None,
    private_key: Optional[str] = None,
    token_id: Optional[int] = None,
) -> Optional[str]:
    """
    Redeem winning CTF tokens from the Gnosis Safe.
    direction: "up"/"yes" or "down"/"no".
    token_id: on-chain ERC-1155 token ID (uint256 from the CLOB/Data API asset field).
              When provided, reads the exact on-chain balance for accurate amounts.
    Returns tx hash on success, None on failure/skip.
    """
    safe_addr   = safe_addr   or os.getenv("PROXY_WALLET", "")
    private_key = private_key or os.getenv("PRIVATE_KEY", "") or os.getenv("POLY_PRIVATE_KEY", "")

    if not safe_addr or not private_key:
        logger.error("[REDEEM] PROXY_WALLET or PRIVATE_KEY/POLY_PRIVATE_KEY not set")
        return None

    safe_addr = Web3.to_checksum_address(safe_addr)
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key
    account = Account.from_key(private_key)

    cid_hex = condition_id.strip()
    if not cid_hex.startswith("0x"):
        cid_hex = "0x" + cid_hex
    try:
        cid_bytes = Web3.to_bytes(hexstr=cid_hex)
    except Exception as e:
        logger.error("[REDEEM] Invalid condition_id: %s", e)
        return None
    if len(cid_bytes) != 32:
        logger.error("[REDEEM] Invalid condition_id length: %d (need 32)", len(cid_bytes))
        return None

    w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))
    ctf  = w3.eth.contract(address=CTF_ADDR, abi=CTF_ABI)
    safe = w3.eth.contract(address=safe_addr, abi=SAFE_ABI)

    d = direction.strip().upper()
    is_up = d in ("YES", "UP")
    index_set = 1 if is_up else 2

    # --- Check actual on-chain token balance ---
    if token_id is not None:
        try:
            on_chain_balance = ctf.functions.balanceOf(safe_addr, token_id).call()
        except Exception as ex:
            logger.warning("[REDEEM] Could not read balanceOf token %s: %s", token_id, ex)
            on_chain_balance = None
        if on_chain_balance is not None:
            if on_chain_balance == 0:
                logger.info(
                    "[REDEEM] Token %s balance is 0 — already redeemed or losing position. Skipping.",
                    str(token_id)[:20],
                )
                return "SKIP_ZERO_BALANCE"
            units = on_chain_balance
            logger.info("[REDEEM] On-chain balance: %d (API reported %.4f)", units, size_matched)
        else:
            units = int(round(size_matched * 1_000_000)) if size_matched > 0 else 50_000_000
    else:
        units = int(round(size_matched * 1_000_000)) if size_matched > 0 else 50_000_000

    # --- Path 1: Standard CTF.redeemPositions (works for both neg_risk and regular) ---
    # NegRisk binary markets use standard CTF conditions under the hood.
    # This avoids the NegRiskAdapter approval complexity.
    ctf_data = ctf.encode_abi(
        "redeemPositions",
        [USDC_ADDR, ZERO_BYTES32, cid_bytes, [index_set]],
    )
    ctf_data_bytes = ctf_data if isinstance(ctf_data, bytes) else Web3.to_bytes(hexstr=ctf_data)

    logger.info(
        "[REDEEM] Trying CTF.redeemPositions for %s (indexSet=%d, %s)",
        cid_hex[:20], index_set, "neg_risk" if neg_risk else "standard",
    )
    result = _exec_safe_tx(w3, safe, account, CTF_ADDR, ctf_data_bytes, label="CTF.redeemPositions", safe_addr=safe_addr)
    if result:
        return result

    # --- Path 2 (neg_risk only): NegRiskAdapter.convertAndRedeemPositions ---
    if not neg_risk:
        logger.warning("[REDEEM] CTF.redeemPositions failed for standard market — cannot redeem.")
        return None

    logger.info("[REDEEM] CTF path failed. Trying NegRiskAdapter.convertAndRedeemPositions ...")

    # Ensure the Safe has approved the NegRisk adapter as an ERC-1155 operator on the CTF.
    # This is a one-time setup; if not set, convertAndRedeemPositions always reverts (GS013).
    try:
        approved = ctf.functions.isApprovedForAll(safe_addr, NEG_RISK_ADDR).call()
    except Exception as ex:
        logger.warning("[REDEEM] isApprovedForAll check failed: %s", ex)
        approved = False

    if not approved:
        logger.info("[REDEEM] Safe has not approved NegRiskAdapter as CTF operator. Setting approval...")
        approval_data = ctf.encode_abi("setApprovalForAll", [NEG_RISK_ADDR, True])
        approval_bytes = approval_data if isinstance(approval_data, bytes) else Web3.to_bytes(hexstr=approval_data)
        approval_tx = _exec_safe_tx(w3, safe, account, CTF_ADDR, approval_bytes, label="CTF.setApprovalForAll", safe_addr=safe_addr)
        if approval_tx:
            logger.info("[REDEEM] Approval set: %s. Waiting for confirmation...", approval_tx[:42])
            time.sleep(8)
        else:
            logger.warning("[REDEEM] Could not set CTF approval for NegRiskAdapter — path 2 will likely fail.")

    neg_risk_contract = w3.eth.contract(address=NEG_RISK_ADDR, abi=NEG_RISK_ABI)
    amounts = [units, 0] if is_up else [0, units]
    neg_data = neg_risk_contract.encode_abi("convertAndRedeemPositions", [cid_bytes, amounts])
    neg_data_bytes = neg_data if isinstance(neg_data, bytes) else Web3.to_bytes(hexstr=neg_data)

    return _exec_safe_tx(w3, safe, account, NEG_RISK_ADDR, neg_data_bytes, label="NegRisk.convertAndRedeemPositions", safe_addr=safe_addr)
