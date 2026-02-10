# Data Lawyer   
**A Chrome Extension that analyzes privacy policies and shows risk and data usage within seconds.**

 Chrome Web Store: https://chromewebstore.google.com/detail/data-lawyer/icghjpppgkopelahjiaihmjipfjmaaoo

---

## What Data Lawyer does?

**Data Lawyer** is a Chrome extension that helps users quickly understand privacy policies by analyzing the content and generating:

- A **risk score (0–100)**
- A **risk level** (LOW / MEDIUM / HIGH)
- A structured breakdown of key privacy topics:
  - Data collected
  - How data is used
  - Sharing & third parties
  - Retention
  - Rights & choices
  - Security
  - International transfers
  - Contact / complaints

Data Lawyer helps users understand what they will be accepting in the terms and conditions and hence be able to make better decisions on whether or not they may want to accept or deny the policies.

---

## Key Features

Analyze privacy policies using URL or pasted policy text  
Detect jurisdiction signals (EU GDPR, US-CA CCPA/CPRA, Kenya DPA, UK GDPR)  
Highlight red flags such as:
- Third-party sharing
- Selling personal data
- Advertising tracking
- Retention ambiguity
- Arbitration clauses
- Weak change-notice protections

Provide clear next-step recommendations  
Works directly inside the browser (Chrome MV3)

---

## How It Works (High Level)

1. The extension detects the active page URL.
2. The user clicks **Analyze**.
3. The extension sends the privacy policy text/URL to the backend API.
4. The backend runs fast rule-based analysis.
5. The extension displays:
   - risk level
   - score
   - summary
   - evidence sections

---

## Tech Stack

### Chrome Extension (Frontend)
- **TypeScript**
- **React**
- **Vite**
- Chrome Extension **Manifest V3**
- Chrome APIs:
  - `chrome.storage`
  - `chrome.tabs`
  - `activeTab`

### Backend API
- **Python**
- **FastAPI**
- **Uvicorn**
- **Gunicorn** (production)
- **Redis** (rate limiting)
- Deployed on **Render**

---

## Project Structure

---

## Local Setup

### 1. Clone the repo

//```bash
git clone https://github.com/IKitheka/data-lawyer.git
cd data-lawyer
 
 
## Extension Setup
Install dependencies-
cd extension
npm install

Build extension-
npm run build

After building you can be able to find the files in-
extension/dist

Load the extension in Chrome-
1.Open Chrome
2.Go to: chrome://extensions
3.Enable Developer mode
4.Click Load unpacked
5.Select:
data-lawyer/extension/dist

Backend Setup (FastAPI)

Create virtual environment-
cd backend
python -m venv .venv
source .venv/bin/activate

Install requirements-
python -m pip install --upgrade pip
pip install -r requirements.txt

Run the backend locally-
PYTHONPATH="$(pwd)" python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000

API Usage (Test with Curl)
Health check-

curl -s http://127.0.0.1:8000/health | python3 -m json.tool

Analyze policy-

curl -s -X POST "http://127.0.0.1:8000/policies/analyze" \
  -H "Content-Type: application/json" \
  -H "X-Client-Id: local_test" \
  -H "X-API-Key: dl_public_beta_2026" \
  -d '{"url":"https://example.com/privacy","text":"We collect personal data and share with partners."}' \
| python3 -m json.tool

Data Lawyer uses these permissions:

storage-store user preferences and cached results
tabs-get the active tab URL and open policy links when requested
activeTab-access current tab only when user clicks the extension
host_permissions-allow requests to the backend API


Data Lawyer is not to be used for legal advice. This is for educational and informational purposes. Whatever the user chooses to do with the information is on their own risk and Data Lawyer will not be responsible for any choices one makes regarding the information provided. The risk score is a heuristic model and may not be perfect so do not trust it blindly. This is a human made extension and may be prone to errors. Do not trust it fully.

Privacy and security
-The extension does not collect any password and does require any login.
-The text is only used for analysis and nothing more.
-All API calls are made over HTTPs.

Google Feedback Form:
https://docs.google.com/forms/d/e/1FAIpQLSeLRJyq1wQyiTNVOQVnyp36zAtWrrf90N2aizpmmqhCpqeG2w/viewform?usp=publish-editor

Enjoy and use it safely.



