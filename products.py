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



class Products:
    def __init__(self):
        self.supabase = supabase
        self.business_id = BUSINESS_ID

    def get_products(self):
        try:
            response = (
                self.supabase
                .table("products")
                .select("*")
                .eq("business_id", self.business_id)
                .execute()
            )

            products = response.data or []

            for product in products:
                product_id = product["id"]
                product["images"] = self._get_product_images(product_id)

            return products

        except Exception as e:
            print(f"Error fetching products: {e}")
            return []

    def _get_product_images(self, product_id):
        try:
            files = (
                self.supabase
                .storage
                .from_("uploaded-files")
                .list(f"products/{product_id}")
            )

            image_urls = []

            for file in files:
                file_path = f"products/{product_id}/{file['name']}"

                public_url = (
                    self.supabase
                    .storage
                    .from_("uploaded-files")
                    .get_public_url(file_path)
                )

                image_urls.append(public_url)

            return image_urls

        except Exception as e:
            print(f"Error fetching images for product {product_id}: {e}")
            return []
