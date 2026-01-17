from supabase import create_client, Client
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
BUSINESS_ID = os.getenv("BUSINESS_ID")
USER_ID = os.getenv("USER_ID")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise Exception("Supabase environment variables not set")

# Create Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


class Cart:
    def __init__(self):
        self.supabase = supabase
        self.business_id = BUSINESS_ID
        self.user_id = USER_ID
        self.items = []
        self.accumulated_total = 0

    def add_to_cart(self, product_id, name, price, image):
        item = {
            "product_id": product_id,
            "name": name,
            "price": float(price),
            "image": image,
            "quantity": 1
        }

        self.items.append(item)
        self.accumulated_total += float(price)

        return {
            "message": "Item added to cart",
            "number_of_items": len(self.items),
            "accumulated_total": float(self.accumulated_total)
        }

    def recalculate_total(self):
        self.accumulated_total = sum(
            item["price"] * item["quantity"] for item in self.items
        )

    def update_quantity(self, product_id, quantity):
        try:
            quantity = int(quantity)

            for item in self.items:
                if item["product_id"] == product_id:
                    item["quantity"] = max(1, quantity)
                    break
            else:
                return {
                    "success": False,
                    "message": "Item not found",
                    "number_of_items": len(self.items),
                    "accumulated_total": float(self.accumulated_total)
                }

            self.recalculate_total()

            return {
                "success": True,
                "message": "Quantity updated",
                "number_of_items": len(self.items),
                "accumulated_total": float(self.accumulated_total)
            }

        except Exception as e:
            print(f"[Cart.update_quantity] Error: {e}")
            return {
                "success": False,
                "message": "Server error",
                "number_of_items": len(self.items),
                "accumulated_total": float(self.accumulated_total)
            }

    def remove_from_cart(self, product_id):
        """
        Removes a product completely from the cart by product_id (UUID-safe).
        """
        try:
            if not product_id:
                return {
                    "success": False,
                    "message": "Missing product_id",
                    "number_of_items": len(self.items),
                    "accumulated_total": float(self.accumulated_total)
                }

            original_len = len(self.items)

            # Filter out the product (NO casting, UUID-safe)
            self.items = [
                item for item in self.items
                if item.get("product_id") != product_id
            ]

            if len(self.items) == original_len:
                return {
                    "success": False,
                    "message": "Item not found",
                    "number_of_items": len(self.items),
                    "accumulated_total": float(self.accumulated_total)
                }

            self.recalculate_total()

            return {
                "success": True,
                "message": "Item removed",
                "number_of_items": len(self.items),
                "accumulated_total": float(self.accumulated_total)
            }

        except Exception as e:
            print(f"[Cart.remove_from_cart] Error: {e}")
            return {
                "success": False,
                "message": "Server error",
                "number_of_items": len(self.items),
                "accumulated_total": float(self.accumulated_total)
            }




