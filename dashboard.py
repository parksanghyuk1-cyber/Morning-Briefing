name: Global Macro Dashboard

on:
  schedule:
    - cron: '0 22 * * 1-5'   # KST 07:00
  workflow_dispatch:

jobs:
  dashboard:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: pip install yfinance requests groq

      - name: Run dashboard
        env:
          GROQ_API_KEY:     ${{ secrets.GROQ_API_KEY }}
          TELEGRAM_TOKEN:   ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python dashboard.py

      - name: Notify on failure
        if: failure()
        env:
          TELEGRAM_TOKEN:   ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: |
          curl -s -X POST \
            "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_CHAT_ID}" \
            -d text="⚠️ 대시보드 전송 실패."
