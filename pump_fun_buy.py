import requests
import base58
import struct
from solana.transaction import AccountMeta, Transaction
from spl.token.instructions import create_associated_token_account, get_associated_token_address, close_account, CloseAccountParams
from solders.pubkey import Pubkey #type: ignore
from solders.instruction import Instruction #type: ignore
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price #type: ignore
from config import payer_keypair, client
from constants import *
from solana.rpc.types import TokenAccountOpts, TxOpts
from utils import get_token_balance, confirm_txn
from coin_data import get_coin_data
from solders.system_program import TransferParams, transfer
from typing import Optional, Union


tip_in_sol = 0.00005
tip_in_lamports = int(tip_in_sol * LAMPORTS_PER_SOL)


def buy(mint_str: str, sol_in: float = 0.01, slippage: int = 30, priority_in_lamports: int = 65000 ) -> bool:
    try:
        # Получаем данные о токене
        coin_data = get_coin_data(mint_str)
        if not coin_data:
            print("Failed to retrieve coin data...")
            return False
        
        #print(f"Payer key pair {payer_keypair}")
        owner = payer_keypair.pubkey()
        mint = Pubkey.from_string(mint_str)

        # Получаем или создаём ATA
        try:
            account_data = client.get_token_accounts_by_owner(owner, TokenAccountOpts(mint))
            token_account = account_data.value[0].pubkey
            token_account_instructions = None
        except:
            token_account = get_associated_token_address(owner, mint)
            token_account_instructions = create_associated_token_account(owner, owner, mint)

        # Расчёт параметров свапа
        virtual_sol_reserves = coin_data['virtual_sol_reserves']
        virtual_token_reserves = coin_data['virtual_token_reserves']
        sol_in_lamports = int(sol_in * LAMPORTS_PER_SOL)
        amount = int(sol_in_lamports * virtual_token_reserves / virtual_sol_reserves)
        print(f"Amount returned from buy: {amount}")

        slippage_adjustment = 1 + (slippage / 100)
        sol_in_with_slippage = sol_in * slippage_adjustment
        max_sol_cost = int(sol_in_with_slippage * LAMPORTS_PER_SOL)
        print("Max Sol Cost:", sol_in_with_slippage)
        
        # Настройка аккаунтов для инструкции
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
            AccountMeta(pubkey=TOKEN_PROGRAM, is_signer=False, is_writable=False),
            AccountMeta(pubkey=RENT, is_signer=False, is_writable=False),
            AccountMeta(pubkey=EVENT_AUTHORITY, is_signer=False, is_writable=False),
            AccountMeta(pubkey=PUMP_FUN_PROGRAM, is_signer=False, is_writable=False)
        ]

        # Формируем инструкцию свапа
        data = bytearray()
        data.extend(bytes.fromhex("66063d1201daebea"))
        data.extend(struct.pack('<Q', amount))
        data.extend(struct.pack('<Q', max_sol_cost))
        data = bytes(data)
        swap_instruction = Instruction(PUMP_FUN_PROGRAM, data, keys)

        # Создаем транзакцию, подписываем
        recent_blockhash = client.get_latest_blockhash().value.blockhash
        txn = Transaction(recent_blockhash=recent_blockhash, fee_payer=owner)
        txn.add(set_compute_unit_price(UNIT_PRICE))
        txn.add(set_compute_unit_limit(priority_in_lamports))
        if token_account_instructions:
            txn.add(token_account_instructions)
        txn.add(swap_instruction)
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
        
        # Сериализуем транзакцию и кодируем в base58 для Jito
        signed_tx_bytes = txn.serialize()
        signed_tx_base58 = base58.b58encode(signed_tx_bytes).decode('utf-8')

        # Формируем запрос к Jito
        jito_request_body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendBundle",
            "params": [[signed_tx_base58]]
        }

        # Отправляем транзакцию в Jito Block Engine
        response = requests.post(
            "https://mainnet.block-engine.jito.wtf/api/v1/bundles",
            json=jito_request_body,
            headers={"Content-Type": "application/json"}
        )

        if response.status_code == 200:
            # Получаем сигнатуру транзакции из подписей
            signature = str(txn.signatures[0])
            print("Transaction Signature", signature)
            return True

        else:
            print("Error sending bundle to Jito:", response.status_code, response.text)
            return False

    except Exception as e:
        print(e)
        return False


def sell(mint_str: str, priority_in_lamports : int = 50000 ,token_balance: Optional[Union[int, float]] = None,  slippage: int = 50, close_token_account: bool = True) -> bool:
    try:
        # Get coin data
        coin_data = get_coin_data(mint_str)
        if not coin_data:
            print("Failed to retrieve coin data...")
            return False
        
        owner = payer_keypair.pubkey()
        mint = Pubkey.from_string(mint_str)

        # Get associated token account
        token_account = get_associated_token_address(owner, mint)

        # Calculate token price
        sol_decimal = 10**9
        token_decimal = 10**6
        virtual_sol_reserves = coin_data['virtual_sol_reserves'] / sol_decimal
        virtual_token_reserves = coin_data['virtual_token_reserves'] / token_decimal
        token_price = virtual_sol_reserves / virtual_token_reserves
        print(f"Token Price: {token_price:.20f} SOL")

        if token_balance == None:
            token_balance = get_token_balance(mint_str)
        print("Token Balance:", token_balance)    
        if token_balance == 0 or token_balance is None:
            print("Token Balance is None!")
            #Since there is nothign to sell we treat this like confirmation
            return True

        # Calculate amount and min_sol_output
        amount = int(token_balance * token_decimal)
        sol_out = float(token_balance) * float(token_price)
        slippage_adjustment = 1 - (slippage / 100)
        sol_out_with_slippage = sol_out * slippage_adjustment
        min_sol_output = int(sol_out_with_slippage * LAMPORTS_PER_SOL)

        MINT = Pubkey.from_string(coin_data['mint'])
        BONDING_CURVE = Pubkey.from_string(coin_data['bonding_curve'])
        ASSOCIATED_BONDING_CURVE = Pubkey.from_string(coin_data['associated_bonding_curve'])
        ASSOCIATED_USER = token_account
        USER = owner

        # Build account key list
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

        # Construct swap instruction
        data = bytearray()
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
            close_account_instructions = close_account(CloseAccountParams(TOKEN_PROGRAM, token_account, owner, owner))
            txn.add(close_account_instructions)

        # Добавляем инструкцию чаевых для Jito:
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

        # Сериализация и отправка в Jito
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
            headers={"Content-Type": "application/json"}
        )

        if response.status_code == 200:
            signature = str(txn.signatures[0])
            print("Transaction Signature", signature)
            return True
        else:
            print("Error sending bundle to Jito:", response.status_code, response.text)
            return False

    except Exception as e:
        print(e)
        return False