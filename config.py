from solana.rpc.api import Client
from solders.keypair import Keypair #type: ignore

PRIV_KEY = "YOUR_PRIVATE_KEY"
RPC = "https://solana-rpc.publicnode.com" #UR RPC, this one works and free

client = Client(RPC)
payer_keypair = Keypair.from_base58_string(PRIV_KEY)

