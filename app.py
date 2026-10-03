from flask import Flask, url_for

app = Flask(__name__)

@app.route("/")
def home():
    return url_for("profile", username="alice")

@app.route("/user/<username>")
def profile(username):
    return f"Profile: {username}"

if __name__ == "__main__":
    app.run()