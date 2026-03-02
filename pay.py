import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import json

from jwcrypto import jwk, jwe


from typing import Optional\

from supabase import create_client, Client
from dotenv import load_dotenv
import os

from inventory import Inventory

import threading
import time



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



LENCO_API_TOKEN = os.getenv("LENCO_API_TOKEN")
LENCO_BASE_URL = os.getenv("LENCO_BASE_URL", "https://api.lenco.co/access/v2").rstrip("/")

if not LENCO_API_TOKEN:
    raise Exception("LENCO_API_TOKEN is not set in environment variables.")


class Pay:
    def __init__(self):
        self.supabase = supabase
        self.business_id = BUSINESS_ID
    """
        def get_payment_token(self, description, amount):
        url = f"{PAYMENT_BASE_URL}/getToken"
        payload = {
            "description": description,
            "amount": amount
        }

        response = requests.post(url, json=payload, timeout=30)

        if response.status_code != 200:
            raise Exception(f"Failed to get token: {response.text}")

        return response.json()
    """
    # ---------------------------
    # LENCO HELPERS
    # ---------------------------
    def get_operator(self, phone):
        """
        Match the TypeScript logic:
        Airtel: (260|0)(97|77)
        MTN:    (260|0)(96|76)
        Zamtel: (260|0)(95|75)
        """
        import re
        clean = re.sub(r"\D", "", phone or "")
        if re.match(r"^(260|0)(97|77)", clean):
            return "airtel"
        if re.match(r"^(260|0)(96|76)", clean):
            return "mtn"
        if re.match(r"^(260|0)(95|75)", clean):
            return "zamtel"
        return ""

    def initiate_lenco_transaction(self, amount, phone_number, reference):
        """
        Sends STK Push to phone. This replaces TechPay token/link generation.
        Endpoint pattern (because your LENCO_BASE_URL already includes /access/v2):
            POST {LENCO_BASE_URL}/collections/mobile-money
        """
        operator = self.get_operator(phone_number)
        if not operator:
            raise Exception("Invalid or unsupported mobile network.")

        endpoint = f"{LENCO_BASE_URL}/collections/mobile-money"

        payload = {
            "amount": float(amount),
            "phone": phone_number,
            "reference": reference,
            "operator": operator,
            "country": "zm",
            "bearer": "merchant",
        }

        headers = {
            "Authorization": f"Bearer {LENCO_API_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }

        resp = requests.post(endpoint, json=payload, headers=headers, timeout=30)

        # Helpful debug like your old code
        print("LENCO INIT STATUS:", resp.status_code)
        print("LENCO INIT RESPONSE:", resp.text)

        if resp.status_code not in (200, 201):
            raise Exception(f"Lenco initiate failed: {resp.text}")

        return resp.json()

    def check_lenco_status(self, reference_or_id):
        """
        Checks payment status.
        GET {LENCO_BASE_URL}/collections/status/{referenceOrId}
        """
        endpoint = f"{LENCO_BASE_URL}/collections/status/{reference_or_id}"

        headers = {
            "Authorization": f"Bearer {LENCO_API_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }

        resp = requests.get(endpoint, headers=headers, timeout=30)

        print("LENCO STATUS CHECK:", resp.status_code)
        print("LENCO STATUS RESPONSE:", resp.text)

        if resp.status_code != 200:
            raise Exception(f"Lenco status check failed: {resp.text}")

        return resp.json()

    def map_lenco_to_payment_status(self, lenco_status_payload: dict) -> str:
        data = (lenco_status_payload or {}).get("data") or {}
        status = data.get("status")
        settlement = data.get("settlementStatus")

        # TRUE FINAL SUCCESS
        if settlement == "settled":
            return "success"

        if status == "successful":
            return "success"

        # FAILED
        if status == "failed":
            return "failed"

        # Everything else is still pending
        return "pending"

    def get_order_amount(self, order_id):
        """
        Get the payable amount for an order.
        """
        try:
            response = (
                self.supabase
                .table("orders")
                .select("total_amount")
                .eq("id", order_id)
                .single()
                .execute()
            )

            if not response.data or response.data.get("total_amount") is None:
                raise Exception("Order amount not found")

            return float(response.data["total_amount"])

        except Exception as e:
            print("Error fetching order amount:", e)
            return None

    def notify_business_payment_received(self, order_id):
        """
        Payment-only notification.
        Order notification is already created at order placement.
        """

        try:
            order_resp = (
                self.supabase
                .table("orders")
                .select("id,total_amount,business_id,customer_id,created_at")
                .eq("id", order_id)
                .single()
                .execute()
            )

            order = order_resp.data
            if not order:
                return None

            message = (
                f"Payment received for Order #{order_id}. "
                f"Amount: ZMW {int(order['total_amount']):,}."
            )

            self.supabase.table("notifications").insert({
                "business_id": order["business_id"],
                "user_id": order["customer_id"],
                "title": "Payment Received",
                "message": message,
                "notification_type": "payment",
                "status": "unread",
                "priority": "normal",
                "category": "payment",
                "action_url": f"/orders/{order_id}",
                "action_label": "View Order",
                "created_at": order["created_at"],
                "updated_at": order["created_at"],
                "tags": ["payment"]
            }).execute()

            return True

        except Exception as e:
            print("Payment notification error:", e)
            return None

    def mark_order_paid(self, transaction_id, amount_paid=None):
        print("\n====== mark_order_paid START ======")
        print("transaction_id:", transaction_id)
        print("amount_paid (from checkout/session):", amount_paid)

        try:
            # 1) Fetch order (DON'T use .single() because it throws if 0 rows)
            print("Fetching order from DB...")
            existing_resp = (
                self.supabase
                .table("orders")
                .select(
                    "id,business_id,customer_id,total_amount,created_at,"
                    "order_payment_status,products,partialAmountTotal,transaction_id"
                )
                .eq("transaction_id", transaction_id)
                .execute()
            )

            if not existing_resp or not existing_resp.data:
                print("⚠️ Order not found yet -> likely not inserted yet. Skipping mark_paid for now.")
                print("====== mark_order_paid END (NOT READY) ======\n")
                return None  # IMPORTANT: caller should keep pending and try again

            # If multiple rows somehow, just take the first
            order = existing_resp.data[0]

            print("Order fetched:", order)
            print(
                "BEFORE UPDATE ->",
                "payment_status:", order.get("order_payment_status"),
                "total_amount:", order.get("total_amount"),
                "partialAmountTotal:", order.get("partialAmountTotal"),
            )

            # 2) If already completed, exit (idempotency)
            if (order.get("order_payment_status") or "").lower() == "completed":
                print("⚠️ Order already marked as completed. Skipping update.")
                print("====== mark_order_paid END (ALREADY COMPLETED) ======\n")
                return order

            # 3) Decide paid amount (YOU want total == partial always)
            paid_amount = amount_paid if amount_paid is not None else order.get("total_amount")
            if paid_amount is None:
                paid_amount = 0

            print("Paid amount that will be saved:", paid_amount)

            # 4) Update order (NO .select() chaining after update)
            print(
                "Updating order: setting order_payment_status=completed, order_status=confirmed, total_amount & partialAmountTotal...")
            update_resp = (
                self.supabase
                .table("orders")
                .update({
                    "order_payment_status": "completed",
                    "order_status": "confirmed",
                    # force your rule: total_amount == partialAmountTotal
                    "total_amount": paid_amount,
                    "partialAmountTotal": paid_amount,
                })
                .eq("id", order["id"])  # update by id (safer than transaction_id)
                .execute()
            )

            if not update_resp or not update_resp.data:
                print("❌ Update failed or returned no data.")
                print("====== mark_order_paid END (ERROR) ======\n")
                return None

            updated_order = update_resp.data[0]
            print("Order AFTER UPDATE:", updated_order)

            # 5) Reduce stock (if products exist)
            print("Starting stock deduction...")
            inventory = Inventory()
            products = updated_order.get("products") or []

            try:
                for item in products:
                    product_id = item.get("product_id")
                    qty = int(item.get("quantity") or 1)
                    print(f"Reducing stock | product_id={product_id}, qty={qty}")

                    if product_id:
                        new_qty = inventory.decrement_stock(product_id, qty)
                        print(f"Stock updated successfully. New quantity={new_qty}")

            except Exception as stock_err:
                print("❌ Stock deduction failed:", stock_err)
                print("Flagging order as stock_issue...")
                self.supabase.table("orders").update({
                    "order_status": "stock_issue"
                }).eq("id", updated_order["id"]).execute()

                print("====== mark_order_paid END (PAID BUT STOCK ISSUE) ======\n")
                return updated_order

            # 6) Continue normal flow
            print("Sending notifications, receipt, and email...")
            self.notify_business_payment_received(updated_order["id"])
            self.create_receipt(updated_order)
            self.send_receipt_email(updated_order)

            print("====== mark_order_paid END (SUCCESS) ======\n")
            return updated_order

        except Exception as e:
            print("❌ mark_order_paid FAILED with exception:", e)
            print("====== mark_order_paid END (ERROR) ======\n")
            return None

    def create_receipt(self, order):
        try:
            payload = {
                "order_id": order["id"],
                "business_id": order["business_id"],
                "customer_id": order["customer_id"],
                "amount": order["total_amount"],
                "currency": "ZMW",
                "status": "paid"
            }

            self.supabase.table("receipts").insert(payload).execute()
            return True

        except Exception as e:
            print("Receipt creation failed:", e)
            return None

    def send_receipt_email(self, order):
        try:
            # Fetch customer email
            customer_resp = (
                self.supabase
                .table("customers")
                .select("email,name")
                .eq("id", order["customer_id"])
                .single()
                .execute()
            )

            customer = customer_resp.data
            if not customer or not customer.get("email"):
                return None

            msg = MIMEMultipart()
            msg["From"] = os.getenv("EMAIL_USER")
            msg["To"] = customer["email"]
            msg["Subject"] = "Payment Successful – Receipt"

            body = f"""
    Hello {customer.get('name', '')},

    Thank you for your payment.

    Order ID: {order['id']}
    Amount Paid: ZMW {order['total_amount']}

    Your order has been successfully received and confirmed.

    Regards,
    InXource Payments
            """

            msg.attach(MIMEText(body, "plain"))

            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.starttls()
            server.login(
                os.getenv("EMAIL_USER"),
                os.getenv("EMAIL_KEY")
            )
            server.send_message(msg)
            server.quit()

            return True

        except Exception as e:
            print("Email sending failed:", e)
            return None


    # ---------------------------
    # LENCO CARD (JWE ENCRYPTION)
    # ---------------------------
    def get_lenco_encryption_key(self) -> dict:
        """
        GET {LENCO_BASE_URL}/encryption-key
        Returns RSA public key in JWK format.
        NOTE: Lenco says key can change anytime; don't cache long-term.
        """
        endpoint = f"{LENCO_BASE_URL}/encryption-key"
        headers = {
            "Authorization": f"Bearer {LENCO_API_TOKEN}",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }

        resp = requests.get(endpoint, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise Exception(f"Failed to get Lenco encryption key: {resp.text}")

        data = resp.json()
        # Most APIs return something like {"status": true, "data": {...jwk...}}
        jwk_data = data.get("data") if isinstance(data, dict) else None
        if not jwk_data:
            # fallback if API returns the jwk directly
            jwk_data = data

        if not isinstance(jwk_data, dict) or not jwk_data.get("n") or not jwk_data.get("e"):
            raise Exception(f"Invalid encryption key response: {data}")

        return jwk_data

    def encrypt_lenco_payload(self, payload: dict, jwk_data: dict) -> str:
        """
        Encrypts payload as JWE Compact Serialization using:
        - alg: RSA-OAEP-256
        - enc: A256GCM
        - cty: application/json
        - kid: jwk_data['kid']
        """
        # Prepare JWK public key
        public_key = jwk.JWK(**jwk_data)

        protected_header = {
            "alg": "RSA-OAEP-256",
            "enc": "A256GCM",
            "cty": "application/json",
            "kid": jwk_data.get("kid"),
        }

        plaintext = json.dumps(payload).encode("utf-8")

        token = jwe.JWE(plaintext, protected=protected_header)
        token.add_recipient(public_key)

        # Compact serialization string
        return token.serialize(compact=True)

    def initiate_lenco_card_transaction(
        self,
        amount: float,
        reference: str,
        email: str,
        first_name: str,
        last_name: str,
        billing: dict,
        card: dict,
        currency: str = "ZMW",
        bearer: str = "merchant",
        redirect_url: str | None = None,
    ) -> dict:
        """
        POST {LENCO_BASE_URL}/collections/card
        Body: {"encryptedPayload": "<JWE>"}

        billing must contain:
          streetAddress, city, postalCode, country (2-letter)
          state optional

        card must contain:
          number, cvv, expiryMonth, expiryYear
        """
        endpoint = f"{LENCO_BASE_URL}/collections/card"

        # Build plaintext payload (THIS MUST BE ENCRYPTED)
        plaintext_payload = {
            "reference": reference,
            "email": email,
            "amount": str(float(amount)),
            "currency": currency,
            "bearer": bearer,
            "customer": {
                "firstName": first_name,
                "lastName": last_name,
            },
            "billing": billing,
            "card": card,
        }

        if redirect_url:
            plaintext_payload["redirectUrl"] = redirect_url

        jwk_data = self.get_lenco_encryption_key()
        encrypted = self.encrypt_lenco_payload(plaintext_payload, jwk_data)

        headers = {
            "Authorization": f"Bearer {LENCO_API_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }

        resp = requests.post(
            endpoint,
            json={"encryptedPayload": encrypted},
            headers=headers,
            timeout=30,
        )

        # Don’t print sensitive info (no card data here anyway)
        print("LENCO CARD INIT STATUS:", resp.status_code)
        print("LENCO CARD INIT RESPONSE:", resp.text)

        if resp.status_code not in (200, 201):
            raise Exception(f"Lenco card initiate failed: {resp.text}")

        return resp.json()
