from dotenv import load_dotenv
load_dotenv()
import os
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_compress import Compress

from cart import Cart
from products import Products
from checkout import Checkout
from pay import Pay
from inventory import Inventory
import uuid
import threading
import time


cart = Cart()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")
Compress(app)
email_user = os.getenv('EMAIL_USER')
email_password = os.getenv('EMAIL_KEY')



@app.context_processor
def inject_cart():
    return {
        "number_of_items": len(cart.items),
        "accumulated_total": cart.accumulated_total
    }


@app.route('/')
def home():
    products_service = Products()
    products = products_service.get_products()

    return render_template(
        'index.html',
        products=products
    )

@app.route("/add-to-cart", methods=["POST"])
def add_to_cart():
    session.pop("order_id", None)

    data = request.json
    product_id = data["id"]

    inventory = Inventory()
    available = inventory.get_available_stock(product_id)

    if available < 1:
        return jsonify({
            "success": False,
            "message": "Out of stock"
        }), 400

    result = cart.add_to_cart(
        product_id=product_id,
        name=data["name"],
        price=data["price"],
        image=data["image"]
    )

    result["success"] = True
    result["available_stock"] = available
    return jsonify(result)




@app.route("/checkout")
def checkout():
    stock_error = session.pop("stock_error", None)

    return render_template(
        "checkout.html",
        cart=cart.items,
        cart_count=len(cart.items),
        cart_total=cart.accumulated_total,
        stock_error=stock_error
    )



@app.route("/update-quantity", methods=["POST"])
def update_quantity():
    session.pop("order_id", None)

    data = request.json

    result = cart.update_quantity(
        product_id=data["product_id"],
        quantity=data["quantity"]
    )

    return jsonify(result)


@app.route("/remove-from-cart", methods=["POST"])
def remove_from_cart():
    session.pop("order_id", None)


    data = request.json or {}
    product_id = data.get("product_id")

    result = cart.remove_from_cart(product_id)
    return jsonify(result)




@app.route("/customer", methods=["GET", "POST"])
def customer():
    checkout = Checkout()

    # SHOW FORM
    if request.method == "GET":
        # If no phone in session, user should not be here
        if not session.get("checkout_phone"):
            return redirect(url_for("checkout"))

        return render_template("customer.html")

    # HANDLE FORM SUBMISSION
    if request.method == "POST":
        try:
            phone = session.get("checkout_phone")
            if not phone:
                return redirect(url_for("checkout"))

            name = request.form.get("name")
            email = request.form.get("email")
            location = request.form.get("location")
            gender = request.form.get("gender")

            customer = checkout.create_customer(
                name=name,
                email=email,
                phone=phone,
                location=location,
                gender=gender
            )

            if not customer:
                # Later we can show an error message
                return redirect(url_for("customer"))

            # Store customer_id for order creation
            session["customer_id"] = customer["id"]

            return redirect(url_for("payout"))

        except Exception as e:
            print("Customer route error:", e)
            return redirect(url_for("customer"))


@app.route("/pay-now", methods=["POST"])
def pay_now():
    checkout = Checkout()

    try:
        # -------------------------------
        # BASIC GUARDS
        # -------------------------------
        if not cart.items:
            return redirect(url_for("checkout"))

        phone = request.form.get("phone")
        if not phone:
            return redirect(url_for("checkout"))

        # Normalize & store phone
        clean_phone = checkout.clean_phone(phone)
        session["checkout_phone"] = clean_phone

        # -------------------------------
        # CAPTURE DELIVERY LOCATION
        # -------------------------------
        delivery_location = request.form.get("delivery_location")
        session["delivery_location"] = delivery_location

        # -------------------------------
        # CAPTURE PER-PRODUCT DATA
        # -------------------------------
        for item in cart.items:
            product_id = item["product_id"]

            # Special instructions
            instruction = request.form.get(f"instruction_{product_id}")
            item["instruction"] = instruction

            # Image upload
            image_file = request.files.get(f"image_{product_id}")
            if image_file and image_file.filename:
                temp_dir = "/tmp"
                os.makedirs(temp_dir, exist_ok=True)

                temp_path = os.path.join(temp_dir, image_file.filename)
                image_file.save(temp_path)

                item["local_image_path"] = temp_path
            else:
                item["local_image_path"] = None

        # -------------------------------
        # CHECK / CREATE CUSTOMER
        # -------------------------------
        result = checkout.check_customer(clean_phone)

        if result["exists"]:
            # Existing customer
            session["customer_id"] = result["customer"]["id"]
            return redirect(url_for("payout"))

        # New customer → continue to customer form
        session.pop("customer_id", None)
        return redirect(url_for("customer"))

    except Exception as e:
        print("Pay now error:", e)
        return redirect(url_for("checkout"))






@app.route("/payout")
def payout():
    # -------------------------------
    # BASIC GUARDS
    # -------------------------------
    if not session.get("customer_id"):
        return redirect(url_for("checkout"))

    # If cart is empty, user should not be here
    if not cart.items:
        return redirect(url_for("checkout"))

    try:
        # Calculate number of items (sum quantities)
        number_of_items = sum(
            (item.get("quantity") or 0) for item in cart.items
        )

        # Read total directly from cart (NO DB)
        accumulated_total = cart.accumulated_total

        return render_template(
            "payout.html",
            accumulated_total=accumulated_total,
            number_of_items=number_of_items
        )

    except Exception as e:
        print("Payout route error:", e)
        return redirect(url_for("checkout"))

#  helper function for process payment
def poll_lenco_and_finalize(reference, checkout_payload):
    """
    Background polling: if settled -> create order + attach items + upload images + notify.
    """
    checkout = Checkout()
    pay_tool = Pay()

    max_attempts = 60
    interval_sec = 5

    for attempt in range(max_attempts):
        time.sleep(interval_sec)

        try:
            status_payload = pay_tool.check_lenco_status(reference)
            payment_status = pay_tool.map_lenco_to_payment_status(status_payload)

            print(f"[LENCO POLL] Attempt {attempt+1}/{max_attempts} Ref={reference} Status={payment_status}")

            if payment_status == "success":
                # Create the order ONLY NOW (settled)
                customer_id = checkout_payload["customer_id"]
                delivery_location = checkout_payload.get("delivery_location", "")
                total_amount = checkout_payload["total_amount"]
                cart_snapshot = checkout_payload["cart_snapshot"]

                # 1) Create order (token = Lenco reference)
                order = checkout.create_order(
                    customer_id=customer_id,
                    delivery_location=delivery_location,
                    total_amount=total_amount,
                    order_token=reference,
                    partial_amount_total=total_amount  # always equal
                )

                if not order:
                    print("[LENCO POLL] Failed to create order in DB.")
                    return

                order_id = order["id"]
                print("[LENCO POLL] Order created:", order_id)

                # 2) Prepare products for notification + upload/attach
                products_for_notification = []
                cart_items = []

                for item in cart_snapshot:
                    products_for_notification.append({
                        "product_id": item["product_id"],
                        "name": item["name"],
                        "price": item["price"],
                        "quantity": item["quantity"],
                        "specialInstructions": item.get("instruction"),
                    })

                    cart_items.append({
                        "product_id": item["product_id"],
                        "quantity": item["quantity"],
                        "specialInstructions": item.get("instruction"),
                        "local_image_path": item.get("local_image_path")
                    })

                # 3) Create notification
                checkout.create_order_notification(
                    user_id=customer_id,
                    business_id=checkout.business_id,
                    order_id=order_id,
                    products=products_for_notification,
                    ordered_at=order["created_at"]
                )

                # 4) Upload images + attach products json
                products_json = checkout.upload_order_images(order_id=order_id, cart_items=cart_items)
                checkout.attach_products_to_order(order_id=order_id, products_json=products_json)

                print("[LENCO POLL] Finalized order successfully.")
                return

            # if failed/pending -> keep polling until timeout (TS behavior)

        except Exception as e:
            print("[LENCO POLL] Polling error:", e)

    print("[LENCO POLL] TIMEOUT: Payment not settled within window.")



@app.route('/process-payment', methods=['POST'])
def process_payment():

    if not session.get("customer_id") or not session.get("checkout_phone"):
        return redirect(url_for("checkout"))

    if not cart.items:
        return redirect(url_for("checkout"))

    checkout = Checkout()
    pay_tool = Pay()

    try:
        phone = session["checkout_phone"]
        customer_id = session["customer_id"]
        delivery_location = session.get("delivery_location", "")

        # 1) TOTAL (and partial = total always)
        total_amount = cart.accumulated_total
        session["total_amount"] = total_amount
        print("DEBUG total_amount:", total_amount)

        # 2) STOCK CHECK (same as before)
        inventory = Inventory()
        insufficient = []

        for item in cart.items:
            product_id = item["product_id"]
            requested_qty = int(item.get("quantity") or 1)
            available = inventory.get_available_stock(product_id)

            if available < requested_qty:
                insufficient.append({
                    "product_id": product_id,
                    "requested": requested_qty,
                    "available": available
                })

        if insufficient:
            session["stock_error"] = insufficient
            return redirect(url_for("checkout"))

        # 3) Snapshot cart for background finalize (important!)
        cart_snapshot = [dict(x) for x in cart.items]

        # 4) Initiate LENCO STK PUSH (reference can be uuid)
        reference_seed = uuid.uuid4().hex  # reference we send to Lenco
        init_payload = pay_tool.initiate_lenco_transaction(
            amount=total_amount,
            phone_number=phone,
            reference=reference_seed
        )

        if init_payload.get("status") is not True:
            print("LENCO rejected request:", init_payload)
            return redirect(url_for("payout"))

        # Lenco returns its own reference in data.reference (as in TS)
        lenco_reference = (init_payload.get("data") or {}).get("reference") or reference_seed

        # 5) Store reference in session so /check-payment-status can verify later
        session["transaction_id"] = lenco_reference

        # 6) Start background polling -> finalize order ONLY when settled
        checkout_payload = {
            "customer_id": customer_id,
            "delivery_location": delivery_location,
            "total_amount": total_amount,
            "cart_snapshot": cart_snapshot
        }

        t = threading.Thread(
            target=poll_lenco_and_finalize,
            args=(lenco_reference, checkout_payload),
            daemon=True
        )
        t.start()

        # 7) Clear cart immediately (prevents duplicate submits)
        cart.items = []
        cart.accumulated_total = 0

        # 8) Redirect user back to payout (we’ll use /check-payment-status to confirm)
        return redirect(url_for("payment_pending"))


    except Exception as e:
        print("Lenco process-payment error:", e)
        return redirect(url_for("payment_pending"))




@app.route("/place-order-pay-at-store", methods=["POST"])
def place_order_pay_at_store():
    # Guards (same vibe as /process-payment)
    if not session.get("customer_id") or not session.get("checkout_phone"):
        return redirect(url_for("checkout"))

    if not cart.items:
        return redirect(url_for("checkout"))

    checkout = Checkout()

    try:
        customer_id = session["customer_id"]
        delivery_location = session.get("delivery_location", "")
        total_amount = cart.accumulated_total

        # Create a token that is NOT TechPay-based (so you can still find the order)
        store_token = f"STORE-{uuid.uuid4().hex[:10].upper()}"

        # 1) Create order (payment status stays "pending" because create_order sets it)
        order = checkout.create_order(
            customer_id=customer_id,
            delivery_location=delivery_location,
            total_amount=total_amount,
            order_token=store_token,
            partial_amount_total=total_amount
        )

        if not order:
            return redirect(url_for("payout"))

        order_id = order["id"]

        # 2) Prepare products (same as your /process-payment flow)
        products_for_notification = []
        cart_items = []

        for item in cart.items:
            products_for_notification.append({
                "product_id": item["product_id"],
                "name": item["name"],
                "price": item["price"],
                "quantity": item["quantity"],
                "specialInstructions": item.get("instruction"),
            })

            cart_items.append({
                "product_id": item["product_id"],
                "quantity": item["quantity"],
                "specialInstructions": item.get("instruction"),
                "local_image_path": item.get("local_image_path")
            })

        # 3) Create notification (you already do this)
        checkout.create_order_notification(
            user_id=customer_id,
            business_id=checkout.business_id,
            order_id=order_id,
            products=products_for_notification,
            ordered_at=order["created_at"]
        )

        # 4) Upload images + attach products JSON
        products_json = checkout.upload_order_images(order_id=order_id, cart_items=cart_items)
        checkout.attach_products_to_order(order_id=order_id, products_json=products_json)

        # 5) Save order id (optional)
        session["order_id"] = order_id

        # 6) Clear cart (important so they don’t re-submit)
        cart.items = []
        cart.accumulated_total = 0

        # For now, redirect to paid page (next step we’ll make a proper “Order received / pay in store” page)
        return redirect(url_for("pay_at_store_success"))


    except Exception as e:
        print("Pay-at-store order error:", e)
        return redirect(url_for("payout"))



@app.route("/payment-confirmation", methods=["POST"])
def payment_confirmation():
    """
    DISABLED:
    This endpoint was used for the old TechPay confirmation flow.
    We now use Lenco (STK push + polling + settlementStatus).
    """
    return jsonify({
        "success": False,
        "message": "This endpoint is disabled. Payments are handled via Lenco."
    }), 410


@app.route("/check-payment-status")
def check_payment_status():
    reference = session.get("transaction_id")
    if not reference:
        return redirect(url_for("checkout"))

    pay_tool = Pay()

    try:
        status_payload = pay_tool.check_lenco_status(reference)
        payment_status = pay_tool.map_lenco_to_payment_status(status_payload)

        data = (status_payload or {}).get("data") or {}
        amount_str = data.get("amount")  # usually like "1.00"
        try:
            amount_paid = float(amount_str) if amount_str is not None else None
        except Exception:
            amount_paid = None

        # ✅ SUCCESS
        if payment_status == "success":
            # 1) Mark the order as paid in Supabase (updates order_payment_status -> completed)
            updated_order = pay_tool.mark_order_paid(reference, amount_paid=amount_paid)

            # 2) Use updated order id for the /paid page
            if updated_order and updated_order.get("id"):
                session["order_id"] = updated_order["id"]
                session.pop("transaction_id", None)
                return redirect(url_for("paid"))

            # Fallback: try to fetch it (rare)
            checkout = Checkout()
            order = checkout.get_order_by_token(reference)
            if order:
                session["order_id"] = order["id"]
                session.pop("transaction_id", None)
                return redirect(url_for("paid"))

            # Rare race condition
            return render_template("payment_pending.html")

        # ❌ FAILED
        if payment_status == "failed":
            session.pop("transaction_id", None)
            return render_template("payment_pending.html", payment_failed=True)

        # ⏳ PENDING
        return render_template("payment_pending.html")

    except Exception as e:
        print("check-payment-status error:", e)
        return render_template("payment_pending.html")




@app.route("/payment/status/<order_number>")
def payment_status(order_number):
    """
    DISABLED:
    This endpoint was used for old TechPay redirect callbacks like:
    /payment/status/123?token=XXXX&status=COMPLETE

    We now use Lenco (STK push + polling + settlementStatus).
    """
    return redirect(url_for("payout"))


@app.route("/payment-pending")
def payment_pending():
    if not session.get("transaction_id"):
        return redirect(url_for("checkout"))

    # Reuse your existing backend checker
    return redirect(url_for("check_payment_status"))



@app.route('/paid')
def paid():
    order_id = session.get("order_id")

    if not order_id:
        return redirect(url_for("checkout"))

    # Clean sensitive session data
    session.pop("transaction_id", None)
    session.pop("order_id", None)
    session.pop("checkout_phone", None)
    session.pop("customer_id", None)

    return render_template('paid.html')


@app.route('/pay-at-store-success')
def pay_at_store_success():
    order_id = session.get("order_id")
    if not order_id:
        return redirect(url_for("checkout"))

    # Clean sensitive session data
    session.pop("transaction_id", None)
    session.pop("order_id", None)
    session.pop("checkout_phone", None)
    session.pop("customer_id", None)



    return render_template('pay_at_store.html')



@app.route('/reviews')
def reviews():
    return render_template('index.html')

@app.route('/contact')
def contact():
    return render_template('contact.html')



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
