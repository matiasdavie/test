from flask import Flask, render_template, redirect, url_for
import RPi.GPIO as GPIO

app = Flask(__name__)

# GPIO setup
GPIO.setmode(GPIO.BCM)

relay1 = 17
relay2 = 22

GPIO.setup(relay1, GPIO.OUT)
GPIO.setup(relay2, GPIO.OUT)


# -------------------------
# Actuator functions
# -------------------------

def forward():
    GPIO.output(relay1, GPIO.HIGH)
    GPIO.output(relay2, GPIO.HIGH)
    print("FORWARD")


def reverse():
    GPIO.output(relay1, GPIO.LOW)
    GPIO.output(relay2, GPIO.LOW)
    print("REVERSE")


def stop():
    GPIO.output(relay1, GPIO.LOW)
    GPIO.output(relay2, GPIO.HIGH)
    print("STOPPED")


# Stop actuator when program starts
stop()


# -------------------------
# Flask routes
# -------------------------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/forward")
def move_forward():
    forward()
    return redirect(url_for("home"))


@app.route("/reverse")
def move_reverse():
    reverse()
    return redirect(url_for("home"))


@app.route("/stop")
def move_stop():
    stop()
    return redirect(url_for("home"))


# -------------------------
# Run Flask
# -------------------------

if __name__ == "__main__":
    try:
        app.run(host="0.0.0.0", port=5000)

    finally:
        stop()
        GPIO.cleanup()