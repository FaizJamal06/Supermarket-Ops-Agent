import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tools.inventory import lookup_product
from src.db.connection import init_db

# Ensure db is initialized and seeded if not already
init_db()

print("Testing Aashirvaad 5kg")
res1 = lookup_product("Aashirvaad 5kg")
print(res1)

print("\nTesting Aashirvaad Atta 5kg")
res2 = lookup_product("Aashirvaad Atta 5kg")
print(res2)
