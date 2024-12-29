import requests
import base58
import struct
import logging
from typing import Optional, Union

from solana.transaction import AccountMeta, Transaction
from spl.token.instructions import create_associated_token_account, get_associated_token_address, close_account, CloseAccountParams
from solders.pubkey import Pubkey
from solders.instruction import Instruction
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price
from solana.rpc.types import TokenAccountOpts, TxOpts
from datetime import datetime

from config import payer_keypair, client
from constants import *
from utils import get_token_balance
from coin_data import get_coin_data
from solders.system_program import TransferParams, transfer

logging.basicConfig(
    level=logging.WARNING,  # Уровень логирования
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("sell_function.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

tip_in_sol = 0.00005
tip_in_lamports = int(tip_in_sol * LAMPORTS_PER_SOL)
owner = payer_keypair.pubkey()

def sell(
    mint_str: str,
    token_balance: Optional[Union[int, float]] = None,
    close_token_account: bool = True,
    sell_percentage: Optional[float] = None,
    slippage: int = 30,
    priority_in_lamports: int = 50000
) -> bool:
    """
    Продаёт токены с указанным mint.
    
    Аргументы:
      mint_str (str): mint адрес токена (строка).
      token_balance (Optional[int,float]): число токенов, которые хотим продать в штуках (целая часть). 
          Если None, то функция сама получит весь баланс кошелька и продаст всё.
      close_token_account (bool): Закрыть ли ATA после продажи. По умолчанию True.
      sell_percentage (Optional[float]): Если указан, воспринимается как доля (%) от общего баланса 
          для продажи. Например 10 = 10%. Если задан, а `token_balance` = None, то продадим ровно 
          sell_percentage процентов от общего баланса. Если `token_balance` задан явно, то приоритет 
          будет у `token_balance`. 
      slippage (int): проскальзывание в %
      priority_in_lamports (int): приоритетная плата (compute unit)
    """
    try:
        logger.debug("Начало функции sell")
        logger.debug(f"Параметры: mint_str={mint_str}, token_balance={token_balance}, "
                     f"sell_percentage={sell_percentage}, close_token_account={close_token_account}, "
                     f"slippage={slippage}, priority_in_lamports={priority_in_lamports}")

        coin_data = get_coin_data(mint_str)
        if not coin_data:
            logger.error("Не удалось получить данные о токене.")
            return False
        logger.debug(f"Данные о токене: {coin_data}")

        owner = payer_keypair.pubkey()
        mint = Pubkey.from_string(mint_str)
        logger.debug(f"Owner Pubkey: {owner}, Mint Pubkey: {mint}")

        token_account = get_associated_token_address(owner, mint)
        logger.debug(f"Associated Token Account: {token_account}")

        sol_decimal = 10**9
        token_decimal = 10**6
        virtual_sol_reserves = coin_data['virtual_sol_reserves'] / sol_decimal
        virtual_token_reserves = coin_data['virtual_token_reserves'] / token_decimal
        if virtual_token_reserves == 0:
            logger.error("virtual_token_reserves = 0, деление на ноль невозможно.")
            return False
        
        token_price = virtual_sol_reserves / virtual_token_reserves
        logger.info(f"Цена токена (примерная): {token_price:.20f} SOL")

        # Если пользователь не передал token_balance, проверяем sell_percentage:
        if token_balance is None:
            wallet_balance = get_token_balance(mint_str)  # Всего токенов на кошельке
            if wallet_balance is None or wallet_balance == 0:
                logger.warning("Token Balance is None or 0, нечего продавать.")
                return True
            # Если указали sell_percentage, продаём % от общего баланса
            if sell_percentage is not None and sell_percentage > 0:
                token_balance = int(wallet_balance * (sell_percentage / 100.0))
                if token_balance <= 0:
                    logger.warning(f"Рассчитанное количество для продажи = {token_balance}, отменяем.")
                    return True
            else:
                # Иначе продаём весь баланс
                token_balance = wallet_balance
        else:
            # Если передан token_balance явно, продаём ровно это количество
            # (sell_percentage игнорируем)
            pass

        print("Token Balance:", token_balance)
        if token_balance == 0 or token_balance is None:
            print("Token Balance is None!")
            return True
        
        amount = int(token_balance * token_decimal)
        sol_out = float(token_balance) * float(token_price)
        slippage_adjustment = 1 - (slippage / 100)
        sol_out_with_slippage = sol_out * slippage_adjustment
        min_sol_output = int(sol_out_with_slippage * LAMPORTS_PER_SOL)
        logger.debug(f"amount={amount}, sol_out={sol_out}, min_sol_output={min_sol_output}")

        MINT = Pubkey.from_string(coin_data['mint'])
        BONDING_CURVE = Pubkey.from_string(coin_data['bonding_curve'])
        ASSOCIATED_BONDING_CURVE = Pubkey.from_string(coin_data['associated_bonding_curve'])
        ASSOCIATED_USER = token_account
        USER = owner
        
        keys = [
            AccountMeta(pubkey=GLOBAL, is_signer=False, is_writable=False),
            AccountMeta(pubkey=FEE_RECIPIENT, is_signer=False, is_writable=True),
            AccountMeta(pubkey=MINT, is_signer=False, is_writable=False),
            AccountMeta(pubkey=BONDING_CURVE, is_signer=False, is_writable=True),
            AccountMeta(pubkey=ASSOCIATED_BONDING_CURVE, is_signer=False, is_writable=True),
            AccountMeta(pubkey=ASSOCIATED_USER, is_signer=False, is_writable=True),
            AccountMeta(pubkey=USER, is_signer=True, is_writable=True),
            AccountMeta(pubkey=SYSTEM_PROGRAM, is_signer=False, is_writable=False),
            AccountMeta(pubkey=ASSOC_TOKEN_ACC_PROG, is_signer=False, is_writable=False),
            AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
            AccountMeta(pubkey=EVENT_AUTHORITY, is_signer=False, is_writable=False),
            AccountMeta(pubkey=PUMP_FUN_PROGRAM, is_signer=False, is_writable=False)
        ]

        data = bytearray()
        # Код инструкции продажи (swap) - меняется в зависимости от вашей программы
        data.extend(bytes.fromhex("33e685a4017f83ad"))
        data.extend(struct.pack('<Q', amount))
        data.extend(struct.pack('<Q', min_sol_output))
        data = bytes(data)
        swap_instruction = Instruction(PUMP_FUN_PROGRAM, data, keys)

        recent_blockhash = client.get_latest_blockhash().value.blockhash
        txn = Transaction(recent_blockhash=recent_blockhash, fee_payer=owner)
        txn.add(set_compute_unit_price(UNIT_PRICE))
        txn.add(set_compute_unit_limit(priority_in_lamports))
        txn.add(swap_instruction)

        if close_token_account:
            # Закрываем счёт после полной продажи
            close_account_instructions = close_account(CloseAccountParams(TOKEN_PROGRAM, token_account, owner, owner))
            txn.add(close_account_instructions)

        # Чаевые jito
        txn.add(
            transfer(
                TransferParams(
                    from_pubkey=owner,
                    to_pubkey=JITOTIP_ACCOUNT,
                    lamports=tip_in_lamports
                )
            )
        )

        txn.sign(payer_keypair)
        signed_tx_bytes = txn.serialize()
        signed_tx_base58 = base58.b58encode(signed_tx_bytes).decode('utf-8')

        jito_request_body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendBundle",
            "params": [[signed_tx_base58]]
        }
        response = requests.post(
            "https://mainnet.block-engine.jito.wtf/api/v1/bundles",
            json=jito_request_body,
            headers={"Content-Type": "application/json"},
        )

        if response.status_code == 200:
            signature = str(txn.signatures[0])
            logger.info(f"Транзакция успешно отправлена. Подпись: {signature}")
            return True
        else:
            logger.error(f"Ошибка при отправке транзакции в Jito: {response.status_code} - {response.text}")
            return False

    except Exception as e:
        logger.exception("Произошла ошибка в функции sell")
        return False
