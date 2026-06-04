#!/bin/bash
# Εκκίνηση bot worker στο background + Streamlit dashboard
python3 bot_worker.py &
streamlit run app.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true
