import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from typing import Optional\

from supabase import create_client, Client
from dotenv import load_dotenv
import os

from inventory import Inventory


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

PAYMENT_BASE_URL = "https://paymentbackend.inxource.com/api/payment"

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

    def get_payment_token(self, description, amount):
        url = f"{PAYMENT_BASE_URL}/getToken"
        payload = {
            "description": description,
            "amount": amount
        }

        response = requests.post(url, json=payload, timeout=30)

        print("GET TOKEN STATUS:", response.status_code)
        print("GET TOKEN RESPONSE:", response.text)

        if response.status_code != 200:
            raise Exception(f"Failed to get token: {response.text}")

        return response.json()

    def initiate_payment(self, token, phone_number, order_id):
        url = f"{PAYMENT_BASE_URL}/initiatePayment"

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        payload = {
            "phoneNumber": phone_number,
            "order_id": order_id
        }

        response = requests.post(url, json=payload, headers=headers, timeout=30)

        if response.status_code != 200:
            raise Exception(f"Payment initiation failed: {response.text}")

        return response.json()



    def check_payment_status(self, order_token):
        url = f"{PAYMENT_BASE_URL}/checkPayment"

        payload = {
            "orderToken": order_token
        }

        response = requests.post(url, json=payload, timeout=30)

        if response.status_code != 200:
            raise Exception(f"Status check failed: {response.text}")

        return response.json()

    def process_payment(self, order_id, phone, amount):
        token_data = self.get_payment_token(
            description=f"Payment for order {order_id}",
            amount=amount
        )

        data = token_data.get("data", {})
        token = data.get("token")
        payment_link = data.get("paymentLink")

        if not token or not payment_link:
            raise Exception(f"Invalid token response: {token_data}")

        # OPTIONAL: initiate mobile money (only if you want STK push)
        # self.initiate_payment(token, phone, order_id)

        return {
            "token": token,
            "payment_link": payment_link
        }

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

    def mark_order_paid(self, order_token, amount_paid):
        print("\n====== mark_order_paid START ======")
        print("order_token:", order_token)
        print("amount_paid (from checkout/session):", amount_paid)

        try:
            # 1️⃣ Fetch order first (idempotency check)
            print("Fetching order from DB...")
            existing_resp = (
                self.supabase
                .table("orders")
                .select(
                    "id,business_id,customer_id,total_amount,"
                    "created_at,order_payment_status,products,partialAmountTotal"
                )
                .eq("orderToken", order_token)
                .single()
                .execute()
            )

            if existing_resp is None:
                print("❌ existing_resp is None")
                return None

            order = existing_resp.data
            print("Order fetched:", order)

            if not order:
                print("❌ Order not found for token:", order_token)
                return None

            print(
                "BEFORE UPDATE ->",
                "payment_status:", order.get("order_payment_status"),
                "total_amount:", order.get("total_amount"),
                "partialAmountTotal:", order.get("partialAmountTotal"),
            )

            # 2️⃣ If already completed, exit (idempotency)
            if (order.get("order_payment_status") or "").lower() == "completed":
                print("⚠️ Order already marked as completed. Skipping update.")
                print("Existing partialAmountTotal:", order.get("partialAmountTotal"))
                return order

            # 3️⃣ Decide what amount to save
            paid_amount = amount_paid if amount_paid is not None else order.get("total_amount")
            print("Paid amount that will be saved:", paid_amount)

            # 4️⃣ Update order: mark paid + save partialAmountTotal
            print("Updating order: setting payment_status=completed and partialAmountTotal...")
            response = (
                self.supabase
                .table("orders")
                .update({
                    "order_payment_status": "completed",
                    "order_status": "confirmed",
                    "partialAmountTotal": paid_amount
                })
                .eq("orderToken", order_token)
                .select(
                    "id,business_id,customer_id,total_amount,"
                    "created_at,order_payment_status,products,partialAmountTotal"
                )
                .single()
                .execute()
            )

            if response is None:
                print("❌ Update response is None")
                return None

            order = response.data
            print("Order AFTER UPDATE:", order)

            if not order:
                print("❌ Update returned no order data")
                return None

            print(
                "AFTER UPDATE ->",
                "payment_status:", order.get("order_payment_status"),
                "total_amount:", order.get("total_amount"),
                "partialAmountTotal:", order.get("partialAmountTotal"),
            )

            # 5️⃣ Reduce stock
            print("Starting stock deduction...")
            inventory = Inventory()
            products = order.get("products") or []

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
                }).eq("orderToken", order_token).execute()

                return order  # Paid but needs manual review

            # 6️⃣ Continue normal flow
            print("Sending notifications, receipt, and email...")
            self.notify_business_payment_received(order["id"])
            self.create_receipt(order)
            self.send_receipt_email(order)

            print("====== mark_order_paid END (SUCCESS) ======\n")
            return order

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


