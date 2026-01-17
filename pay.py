import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from typing import Optional\

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

    def mark_order_paid(self, order_token):
        try:
            response = (
                self.supabase
                .table("orders")
                .update({
                    "order_payment_status": "completed",
                    "order_status": "confirmed"
                })
                .eq("orderToken", order_token)
                .select()
                .single()
                .execute()
            )

            order = response.data
            if not order:
                return None

            self.notify_business_payment_received(order["id"])
            self.create_receipt(order)
            self.send_receipt_email(order)

            return order

        except Exception as e:
            print("Failed to mark order paid:", e)
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


