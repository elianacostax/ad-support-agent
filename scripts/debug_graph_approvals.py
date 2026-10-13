import requests

from app.services.microsoft_auth import acquire_app_token


def main():
    token_result = acquire_app_token()
    access_token = token_result["access_token"]

    response = requests.get(
        "https://graph.microsoft.com/beta/solutions/approval/approvalItems",
        headers={
            "Authorization": f"Bearer {access_token}",
        },
        timeout=30,
    )

    print("STATUS:", response.status_code)
    print("RESPONSE:")
    print(response.text)


if __name__ == "__main__":
    main()