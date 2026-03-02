from supabase import create_client, Client
from dotenv import load_dotenv
import os

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BUSINESS_ID = os.getenv("BUSINESS_ID")  # CKC business id in your .env

if not SUPABASE_URL or not SUPABASE_KEY:
    raise Exception("SUPABASE_URL / SUPABASE_KEY not set")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


class Inventory:
    def __init__(self):
        self.supabase = supabase
        self.business_id = BUSINESS_ID

    def get_available_stock(self, product_id):
        try:
            print("\n[STOCK CHECK] business_id:", self.business_id, "product_id:", product_id)

            resp = (
                self.supabase
                .table("stock_table")
                .select("quantity")
                .eq("business_id", self.business_id)
                .eq("product_id", product_id)
                .limit(1)
                .execute()
            )

            print("[STOCK CHECK] raw resp:", resp)

            if resp is None:
                print("[STOCK CHECK] ❌ resp is None")
                return 0

            # resp.data will be a LIST
            if not resp.data:
                print("[STOCK CHECK] ✅ No row found -> stock = 0")
                return 0

            qty = resp.data[0].get("quantity") or 0
            print("[STOCK CHECK] ✅ Found quantity:", qty)
            return int(qty)

        except Exception as e:
            print("[STOCK CHECK] ❌ Error getting stock from Supabase:", e)
            return 0

    def decrement_stock(self, product_id, qty, retries = 3) :
        """
        Decrement stock using optimistic locking (no SQL function).
        Returns the new quantity.
        Raises Exception if insufficient stock or too much contention.
        """
        qty = int(qty)
        if qty <= 0:
            raise Exception("Invalid quantity")

        for _ in range(retries):
            # 1) Read current quantity (single row per product)
            resp = (
                self.supabase
                .table("stock_table")
                .select("quantity")
                .eq("business_id", self.business_id)
                .eq("product_id", product_id)
                .maybe_single()
                .execute()
            )

            if not resp.data:
                raise Exception("No stock row found")

            current_qty = int(resp.data.get("quantity") or 0)

            if current_qty < qty:
                raise Exception("Insufficient stock")

            new_qty = current_qty - qty

            # 2) Update ONLY IF quantity is still the same (optimistic lock)
            update_resp = (
                self.supabase
                .table("stock_table")
                .update({"quantity": new_qty})
                .eq("business_id", self.business_id)
                .eq("product_id", product_id)
                .eq("quantity", current_qty)
                .select("quantity")  # 🔥 THIS FIXES EVERYTHING
                .execute()
            )

            # If update affected 1 row, you're done
            if update_resp.data:
                return new_qty

        raise Exception("Stock update conflict, please retry")


# testing
"""
test = Inventory()
print(test.get_available_stock("d77c7959-93b5-47a0-8f12-8bcb57187ae6"))
"""


