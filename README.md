🌍 Technex-2026 – Nova Neurals
Renewable Energy Marketplace Portal

👥 Team Members

Arya Lunawat

Divya Jain

Mahi Jaiswal

Pratikshya Dash

DEPLOYED LINK - nexusenergy-production.up.railway.app

🌱 Renewable Energy Marketplace Portal

A decentralized, climate-adaptive web platform that connects renewable energy producers (solar rooftop owners, wind farms, biogas plants) with consumers and investors through a dynamic peer-to-peer trading engine.

This project simulates a smart-grid ecosystem with weather-reactive pricing, hyper-local energy optimization, escrow-based settlement, and carbon impact tracking.

📖 Overview

The Renewable Energy Marketplace Portal transforms passive energy consumers into prosumers (producers + consumers) and enables decentralized green energy exchange.

Unlike static energy trading systems, our platform dynamically adapts to:

Real-time weather conditions

Supply–demand fluctuations

Local grid dynamics

Sustainability metrics

The system models real-world smart-grid economics in a hackathon-ready prototype.

🎯 Objective

Promote decentralized energy exchange

Enable peer-to-peer green energy trading

Encourage community-based sustainability

Simulate a climate-adaptive smart energy ecosystem

🚀 Core Features
🔹 1. Weather-Integrated Dynamic Pricing

Energy prices automatically adjust based on:

Location-based weather conditions

Cloud cover (affects solar generation)

Wind speed (affects wind output)

Supply–demand ratio

Government tariff cap enforcement

💡 Pricing Formula
Energy Price = BasePrice × (Demand / Supply) × WeatherFactor

Prices recalculate periodically to simulate real smart-grid market behavior.

🔹 2. Real-Time Renewable Grid Simulation

Solar nodes pulse based on sunlight intensity

Wind turbines rotate based on wind speed

Biogas nodes provide steady baseload supply

Digital twin representation of the local grid

This creates a visual and logical simulation of a renewable energy ecosystem.

🔹 3. Hyper-Local Energy Optimization

Neighborhood-based producer–consumer matching

Distance-based micro transmission fee

Community energy pooling

Local load balancing to reduce transmission losses

The system prioritizes local trades to simulate efficient microgrid economics.

🔹 4. Secure Peer-to-Peer Settlement

Escrow-based payment mechanism

SHA-256 transaction hashing

Transparent digital ledger system

Payments are released only after trade confirmation, ensuring secure and trusted exchanges.

🔹 5. Sustainability Intelligence

CO₂ savings calculation per trade

Renewable energy usage tracking

Sustainability scoring system

Recognition tiers for eco-conscious users

The platform incentivizes responsible energy consumption.

🏗️ System Architecture
Users (Producer / Consumer / Investor / Admin)
                    ↓
              Flask Backend
                    ↓
           Marketplace Engine
                    ↓
      Pricing Logic + Escrow Layer
                    ↓
     Database & Transaction Ledger
🛠 Tech Stack
Backend

Python

Flask

Frontend

HTML (Jinja Templates)

CSS

JavaScript

Database

SQLite

Security & Logic

SHA-256 Hashing

Escrow Simulation

Demand–Supply Pricing Engine

📂 Project Structure
project-folder/
│
├── app.py
├── seed.py
├── requirements.txt
├── .env
│
├── templates/
│   ├── index.html
│   ├── producer.html
│   ├── consumer.html
│   ├── investor.html
│   ├── admin.html
│   └── ledger.html
│
└── static/
⚙️ Installation & Setup
1️⃣ Clone the Repository
git clone https://github.com/your-username/your-repository.git
cd your-repository
2️⃣ Create a Virtual Environment
python -m venv venv
Activate

Windows

venv\Scripts\activate

Mac/Linux

source venv/bin/activate
3️⃣ Install Dependencies
pip install -r requirements.txt
4️⃣ Initialize the Database
python seed.py
5️⃣ Run the Application
python app.py

Open in browser:

http://127.0.0.1:5000/
🔐 Security Model

Every transaction generates a unique SHA-256 hash

Escrow mechanism ensures conditional fund release

All trades are logged in a transparent digital ledger

This ensures integrity, traceability, and trust.

🌍 Environmental Impact

The platform promotes sustainability by:

Encouraging renewable energy adoption

Calculating carbon emissions avoided

Incentivizing responsible consumption

Supporting decentralized green communities

🔮 Future Enhancements

Blockchain-based smart contract integration

IoT smart meter connectivity

Live weather API integration for production sync

AI-based demand forecasting

Mobile application deployment

📌 Project Status

Hackathon prototype demonstrating decentralized renewable energy trading logic, climate-adaptive pricing, and smart-grid simulation.
