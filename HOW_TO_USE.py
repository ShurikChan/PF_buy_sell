from pump_fun_buy import buy
from pump_fun_sell import sell


# Buy Example

#buy("mint_address", amount_in_sol = 0.1,)

#sell("mint_address") - if u want sell all tokens u write only mint address, it will sell all the tokens

#sell("mint_address", sell_percentage = 10, close_token_account = False) - if u want sell 10% or any another % like 10-20-30-40-50 and so on

# in utils.py u can find confirm_txn function that checks if tx was successful or not, use it if u need

# also u should know that pf buy/sell funcs send TXs using JITO bundle, so they r faster than usual
# if u don't need jito, u should modify the code and delete jito logic
# current slippage is 30, u should set it less, if slippage 5 for example it often gives u unsuccess txs, it's better to use private nodes like helius or quicknode for example
# test this funcs before use them in ur bots
