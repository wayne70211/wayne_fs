from typing import Dict, Tuple
import typing
if typing.TYPE_CHECKING:
    from journal import Journal

class Transaction:
    def __init__(self, journal: "Journal", tid: int):
        self.journal = journal
        self.tid = tid
        self.write_buffer: Dict[int, Tuple[str, bytes]] = {} 
        print(f"Transaction {self.tid} started.")

    def write(self, final_block_addr: int, block_data: bytes, block_type: str = "Unknown"):
        print(f"  - tx {self.tid}: logging write for '{block_type}' to block {final_block_addr}")
        self.write_buffer[final_block_addr] = (block_type, block_data)