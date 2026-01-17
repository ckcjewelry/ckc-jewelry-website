from dotenv import load_dotenv
load_dotenv()
import os
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_compress import Compress

from cart import Cart
from products import Products
from checkout import Checkout
from pay import Pay

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

    result = cart.add_to_cart(
        product_id=data["id"],
        name=data["name"],
        price=data["price"],
        image=data["image"]
    )

    return jsonify(result)


@app.route("/checkout")
def checkout():
    return render_template(
        "checkout.html",
        cart=cart.items,
        cart_count=len(cart.items),
        cart_total=cart.accumulated_total
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

        # -------------------------------
        # 1. CALCULATE TOTAL
        # -------------------------------
        total_amount = cart.accumulated_total

        # -------------------------------
        # 2. GET PAYMENT TOKEN FIRST
        # -------------------------------
        payment = pay_tool.process_payment(
            order_id="TEMP",
            phone=phone,
            amount=total_amount
        )

        token = payment["token"]
        payment_link = payment["payment_link"]

        # -------------------------------
        # 3. CREATE ORDER (NO PRODUCTS YET)
        # -------------------------------
        order = checkout.create_order(
            customer_id=customer_id,
            delivery_location=delivery_location,
            total_amount=total_amount,
            order_token=token
        )

        if not order:
            return redirect(url_for("checkout"))

        order_id = order["id"]

        # -------------------------------
        # 4. PREPARE PRODUCTS (TS SHAPE)
        # IMPORTANT: includes name + price
        # -------------------------------
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

        # -------------------------------
        # 5. CREATE ORDER NOTIFICATION
        # -------------------------------
        checkout.create_order_notification(
            user_id=customer_id,
            business_id=checkout.business_id,
            order_id=order_id,
            products=products_for_notification,
            ordered_at=order["created_at"]
        )

        # -------------------------------
        # 6. UPLOAD IMAGES + ATTACH PRODUCTS
        # -------------------------------
        products_json = checkout.upload_order_images(
            order_id=order_id,
            cart_items=cart_items
        )

        checkout.attach_products_to_order(
            order_id=order_id,
            products_json=products_json
        )

        # -------------------------------
        # 7. SAVE SESSION
        # -------------------------------
        session["order_id"] = order_id
        session["transaction_id"] = token

        # -------------------------------
        # 8. CLEAR CART
        # -------------------------------
        cart.items = []
        cart.accumulated_total = 0

        # -------------------------------
        # 9. REDIRECT TO PAYMENT
        # -------------------------------
        return redirect(payment_link)

    except Exception as e:
        print("Payment processing error:", e)
        return redirect(url_for("payout"))



@app.route("/payment-confirmation", methods=["POST"])
def payment_confirmation():
    data = request.json or {}

    order_token = data.get("order_token")
    payment_status = data.get("status")

    if not order_token or payment_status != "SUCCESS":
        return jsonify({"error": "Invalid confirmation"}), 400

    pay_tool = Pay()

    updated = pay_tool.mark_order_paid(order_token)

    if not updated:
        return jsonify({"error": "Order update failed"}), 500

    return jsonify({"message": "Order confirmed"}), 200


@app.route("/check-payment-status")
def check_payment_status():
    order_token = session.get("transaction_id")

    if not order_token:
        return redirect(url_for("checkout"))

    pay_tool = Pay()

    status = pay_tool.check_payment_status(order_token)

    if status.get("status") == "SUCCESS":
        pay_tool.mark_order_paid(order_token)
        return redirect(url_for("paid"))

    return redirect(url_for("payout"))




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



@app.route('/reviews')
def reviews():
    return render_template('index.html')

@app.route('/contact')
def contact():
    return render_template('contact.html')



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
