# Open the local textile demo

Open **http://127.0.0.1:8180/dashboard/** and sign in as **demo**. On this Mac, copy the password from the local environment to the clipboard without printing it. Paste it into the password field.

```bash
source "$HOME/.local/state/clandexis-demo/session.env"
printf %s "$DASHBOARD_DEMO_PASSWORD" | pbcopy
```

The personal demo starts with 200 Cotton Shirt (L) at ₹200 each, 150 Linen Kurta (M) at ₹450, zero transactions and zero outstanding for Suresh Traders and Ramesh Textiles. The launcher uses the existing textile seed and opening-balance routine in the local Firestore emulator.

1. Open **Live Extractor**. Paste the sale below or speak its Telugu equivalent into an audio file and select **Upload audio**. A selected file takes precedence over text. Click **Run Extraction & Validation** once.
2. The extraction receipt shows the persisted candidate ID, validation status and posting status. **ACCEPTED / PENDING_CONFIRMATION** means the sale awaits your decision. Click **View candidates**, locate the same ID and click **Commit** after checking the details. For item-level review, open **Overview**. Check the buyer, quantity, price, actual payment and credit on the candidate. For a held candidate, correct the displayed facts, enter a review note, and click **Save review**. In Overview, **Authorize & Commit to Ledger** performs the same confirmation. For a rejected result, correct the input and extract again.
3. Open **Inventory** for 150 shirts. **Overview** shows ₹10,000 sales, ₹2,000 UPI and ₹8,000 outstanding. Under **Transactions**, click **Download invoice** next to the sale. Its total, paid and due are ₹10,000, ₹2,000 and ₹8,000.
4. Return to **Live Extractor**, clear the file selection if necessary, paste the repayment below and repeat review and confirmation. Sales stay ₹10,000 and stock stays 150. Collections become ₹3,000, cumulative UPI becomes ₹5,000 and outstanding becomes ₹5,000. The original sale invoice retains its amounts at posting.

**Sale, English**

> Sold 50 Cotton Shirt (L) to Suresh Traders at INR 200 each, total INR 10000. Received INR 2000 by UPI. Remaining INR 8000 on credit.

**Sale, Telugu**

> సురేష్ ట్రేడర్స్‌కి యాభై కాటన్ షర్టులు అమ్మాను. ఒక్కో షర్టు ధర రెండు వందల రూపాయలు. మొత్తం పది వేల రూపాయలు. యూపీఐ ద్వారా రెండు వేల రూపాయలు వచ్చాయి. మిగిలిన ఎనిమిది వేల రూపాయలు బాకీ.

**Repayment**

> Suresh Traders repaid INR 3000 by UPI today towards his outstanding credit. No new items sold.

The project sample at `../voice-telugu.mp3` worked as a real media upload. Gemini interpreted it as a purchase requiring review. Use the supplied sale wording for the stock scenario. The app uses the configured Gemini API key and model. Provider failures appear as processing errors. No fixture fallback is enabled.

## Start or reset

Run from the repository root. The local launcher uses the cached Java 21 runtime and Firestore emulator JAR on this Mac. On another machine set `JAVA_BIN` and `FIRESTORE_EMULATOR_JAR`. The emulator binds to loopback on 8185 and FastAPI serves the built dashboard on 8180.

```bash
cd /Users/avinash/Desktop/clandexis/team_clandexis
.venv/bin/python -m pip install -r requirements-dev.txt
(cd dashboard && npm ci && npm run build)
scripts/local_demo.sh
```

For a clean run, stop the demo web server with Ctrl-C in its terminal. If it is running in the background, use the command below, then run `scripts/local_demo.sh fresh`. This selects a new isolated merchant and rotates the local credentials. Copy the new password using the first command block. Earlier demo records remain in the emulator until it shuts down.

```bash
kill -TERM "$(cat "$HOME/.local/state/clandexis-demo/web.pid")"
scripts/local_demo.sh fresh
```

## Release verification

```bash
PATH="$HOME/.cache/clandexis-tools/jdk-21.0.13.1+2-jre/Contents/Home/bin:$PATH" FIREBASE_BIN="$HOME/.cache/clandexis-tools/fb/node_modules/.bin/firebase" tests/run_ledger_tests.sh
(cd dashboard && npm run build)
.venv/bin/python -m pip check
```

Production uses the Dockerfile's FastAPI entry point, built dashboard assets and Cloud Run port. Configure a Gemini credential, Firestore access, HTTPS trusted origin, session/demo secrets and a separate send token. WhatsApp additionally requires its verify token, app secret, outbound token, phone number ID and subscribed webhook. Private invoice delivery requires GCS object access and a runtime credential capable of V4 URL signing. The local demo uses an authenticated filesystem download. Cloud resources, IAM and live message delivery were outside this run.
