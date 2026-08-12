"""Bird class backed by the Some Random API bird endpoint."""

import requests

API_URL = "https://some-random-api.com/animal/birb"
REQUEST_TIMEOUT = 10  # seconds
# The API's edge (Cloudflare) blocks requests without a browser-like UA.
HEADERS = {"User-Agent": "Mozilla/5.0 (daily-code-lab bird exercise)"}


class Bird:
    def __init__(self):
        self.image = None
        self.fact = None
        self._fetch()

    def _fetch(self):
        response = requests.get(API_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        self.image = data["image"]
        self.fact = data["fact"]

    def __repr__(self):
        return f"Bird(image={self.image!r}, fact={self.fact!r})"


def main():
    birds = [Bird() for _ in range(3)]
    for i, bird in enumerate(birds, start=1):
        print(f"Bird {i}")
        print(f"  image: {bird.image}")
        print(f"  fact:  {bird.fact}")


if __name__ == "__main__":
    main()
