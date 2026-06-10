/**
 * #SpeakingMyName Outreach Email Sender
 *
 * Sends drafted partnership emails from the "#SpeakingMyName Outreach" tab.
 * Sends all rows where Status = "DRAFTED" (no manual approval step).
 * Skips rows already marked as SENT.
 *
 * The workflow is:
 *   1. generate_smn_emails.py  → generates drafts (Status = DRAFTED)
 *   2. This script              → sends all drafted emails (Status = SENT)
 *
 * SETUP:
 * 1. Paste this into Extensions > Apps Script in your Google Sheet
 * 2. Adjust BATCH_START / BATCH_SIZE below if needed
 * 3. Run sendBatch() from the editor, or use the #SpeakingMyName menu
 *
 * COLUMN MAPPING:
 *   A = Full Name          F = Status           J = Follow-up?
 *   B = Title              G = Notes            K = Sent At
 *   C = Association Name   H = Email Subject
 *   D = Type               I = Personalized Email
 *   E = Contact Email
 */

// ═══════════════════════════════════════════════════
// CONFIG — CHANGE THESE
// ═══════════════════════════════════════════════════

var BATCH_START = 2;      // Row number to start from (row 2 = first org)
var BATCH_SIZE  = 50;     // How many rows to scan in this batch
var SHEET_NAME  = "#SpeakingMyName Outreach";

// Default sender name (used in GmailApp.sendEmail)
var DEFAULT_SENDER_NAME = "Hana Figueroa from SpeakHire";

// ═══════════════════════════════════════════════════
// EMAIL TRACKING — Azure Functions (open + click)
// ═══════════════════════════════════════════════════

var TRACKING_BASE_URL = "https://YOUR_FUNCTION.azurewebsites.net";
var CAMPAIGN_SLUG     = "speaking_my_name";

/**
 * Encode recipient metadata into a URL-safe tracking ID.
 * The Azure Function decodes this to record who opened/clicked.
 */
function encodeTrackingId(email, name, orgName, campaign, linkUrl, linkText) {
  var payload = {
    e: email || "",
    n: name || "",
    o: orgName || "",
    c: campaign || "unknown",
  };
  if (linkUrl) {
    payload.l = linkUrl;
    payload.t = linkText || "";
    payload.i = (email || "") + "|" + (campaign || "");
  }
  var encoded = Utilities.base64Encode(JSON.stringify(payload), Utilities.Charset.UTF_8);
  return encoded.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** Return a 1×1 tracking pixel <img> tag for open tracking. */
function getTrackingPixel(email, name, orgName, campaign) {
  var id = encodeTrackingId(email, name, orgName, campaign);
  return '<img src="' + TRACKING_BASE_URL + '/api/o/' + id + '"' +
    ' width="1" height="1" alt=""' +
    ' style="display:none !important;visibility:hidden !important;' +
    'opacity:0 !important;width:1px !important;height:1px !important;" />';
}

// ═══════════════════════════════════════════════════
// COLUMN MAPPING (1-based: A=1, B=2, ..., K=11)
// ═══════════════════════════════════════════════════

var COL_FULL_NAME       = 1;   // A
var COL_TITLE           = 2;   // B
var COL_ASSOC_NAME      = 3;   // C
var COL_TYPE            = 4;   // D
var COL_EMAIL           = 5;   // E
var COL_STATUS          = 6;   // F
var COL_NOTES           = 7;   // G
var COL_EMAIL_SUBJECT   = 8;   // H
var COL_PERSONALIZED    = 9;   // I
var COL_FOLLOWUP        = 10;  // J
var COL_SENT_AT         = 11;  // K

// ═══════════════════════════════════════════════════
// MAIN SEND FUNCTION
// ═══════════════════════════════════════════════════

function sendBatch() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  if (!sheet) {
    SpreadsheetApp.getUi().alert(
      'Sheet "' + SHEET_NAME + '" not found. ' +
      'Make sure the "#SpeakingMyName Outreach" tab exists.'
    );
    return;
  }

  var lastRow = sheet.getLastRow();
  var lastCol = COL_SENT_AT;  // K
  var data = sheet.getRange(1, 1, lastRow, lastCol).getValues();

  var sent    = 0;
  var skipped = 0;
  var errors  = 0;
  var endRow  = Math.min(BATCH_START + BATCH_SIZE - 1, lastRow);

  for (var i = BATCH_START - 1; i < endRow; i++) {
    var row = data[i];

    var status      = String(row[COL_STATUS - 1]      || "").trim();
    var email       = String(row[COL_EMAIL - 1]        || "").trim();
    var subject     = String(row[COL_EMAIL_SUBJECT - 1] || "").trim();
    var body        = String(row[COL_PERSONALIZED - 1]  || "").trim();
    var assocName   = String(row[COL_ASSOC_NAME - 1]    || "").trim();
    var fullName    = String(row[COL_FULL_NAME - 1]     || "").trim();

    // --- SKIP CHECKS ---

    // Already sent
    if (status === "SENT") {
      skipped++;
      continue;
    }

    // Only send DRAFTED rows (no manual approval needed)
    if (status !== "DRAFTED") {
      continue;
    }

    // No email address
    if (!email || email.indexOf("@") === -1) {
      errors++;
      sheet.getRange(i + 1, COL_STATUS).setValue("ERROR");
      sheet.getRange(i + 1, COL_NOTES).setValue(
        (row[COL_NOTES - 1] || "") + " | ERROR: No valid email"
      );
      continue;
    }

    // No body to send
    if (!body) {
      errors++;
      sheet.getRange(i + 1, COL_STATUS).setValue("ERROR");
      sheet.getRange(i + 1, COL_NOTES).setValue(
        (row[COL_NOTES - 1] || "") + " | ERROR: No email body"
      );
      continue;
    }

    // No subject — use a default
    if (!subject) {
      subject = assocName + " + #SpeakingMyName on June 16";
    }

    // --- SEND ---
    try {
      // Build basic HTML from plain text + tracking pixel
      var htmlBody = body
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\n/g, "<br>");
      htmlBody += getTrackingPixel(email, fullName, assocName, CAMPAIGN_SLUG);
      htmlBody = '<div style="font-family:Arial,sans-serif;font-size:14px;color:#222;">' +
                 htmlBody + '</div>';

      GmailApp.sendEmail(email, subject, body, {
        htmlBody: htmlBody,
        name: DEFAULT_SENDER_NAME,
      });

      // Mark as sent
      var timestamp = new Date().toLocaleString();
      sheet.getRange(i + 1, COL_STATUS).setValue("SENT");
      sheet.getRange(i + 1, COL_SENT_AT).setValue(timestamp);
      sent++;

      Logger.log("Sent: " + assocName + " → " + email + " — " + subject);

      // Rate limit: sleep 1 second between sends
      Utilities.sleep(1000);
    } catch (e) {
      errors++;
      var errMsg = e.toString().substring(0, 200);
      sheet.getRange(i + 1, COL_STATUS).setValue("ERROR");
      sheet.getRange(i + 1, COL_NOTES).setValue(
        (row[COL_NOTES - 1] || "") + " | SEND ERROR: " + errMsg
      );
      Logger.log("ERROR row " + (i + 1) + ": " + errMsg);
    }
  }

  // --- SUMMARY ---
  var msg =
    "#SpeakingMyName batch complete!\n\n" +
    "Sent: "    + sent    + "\n" +
    "Skipped: " + skipped + "\n" +
    "Errors: "  + errors  + "\n\n" +
    "Scanned rows " + BATCH_START + " to " + endRow +
    " of " + lastRow;

  SpreadsheetApp.getUi().alert(
    "Mail Merge Done",
    msg,
    SpreadsheetApp.getUi().ButtonSet.OK
  );
  Logger.log(msg);
}

// ═══════════════════════════════════════════════════
// PREVIEW — dry run: show what WOULD be sent
// ═══════════════════════════════════════════════════

function previewBatch() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  if (!sheet) {
    SpreadsheetApp.getUi().alert('Sheet "' + SHEET_NAME + '" not found.');
    return;
  }

  var lastRow = sheet.getLastRow();
  var lastCol = COL_SENT_AT;
  var data = sheet.getRange(1, 1, lastRow, lastCol).getValues();

  var wouldSend  = 0;
  var wouldSkip  = 0;
  var preview    = [];
  var endRow     = Math.min(BATCH_START + BATCH_SIZE - 1, lastRow);

  for (var i = BATCH_START - 1; i < endRow; i++) {
    var row = data[i];

    var status   = String(row[COL_STATUS - 1]     || "").trim();
    var email    = String(row[COL_EMAIL - 1]       || "").trim();
    var subject  = String(row[COL_EMAIL_SUBJECT - 1] || "").trim();
    var body     = String(row[COL_PERSONALIZED - 1] || "").trim();
    var assocName = String(row[COL_ASSOC_NAME - 1]  || "").trim();

    if (status === "SENT") { wouldSkip++; continue; }
    if (status !== "DRAFTED") { continue; }
    if (!email || email.indexOf("@") === -1) { continue; }
    if (!body) { continue; }

    wouldSend++;
    preview.push(
      "Row " + (i + 1) + " | " + assocName +
      " | To: " + email +
      " | Subject: " + (subject || "(using default)").substring(0, 70)
    );
  }

  var msg =
    "DRY RUN — no emails were sent.\n\n" +
    "Would send: " + wouldSend + "\n" +
    "Would skip: " + wouldSkip + "\n\n" +
    (preview.length > 0
      ? "Preview (first " + Math.min(15, preview.length) + "):\n" +
        preview.slice(0, 15).join("\n") +
        (preview.length > 15
          ? "\n... and " + (preview.length - 15) + " more"
          : "")
      : "No approved emails found in rows " +
        BATCH_START + "-" + endRow);

  SpreadsheetApp.getUi().alert(
    "#SpeakingMyName Preview",
    msg,
    SpreadsheetApp.getUi().ButtonSet.OK
  );
  Logger.log(msg);
}

// ═══════════════════════════════════════════════════
// CONVENIENCE FUNCTIONS
// ═══════════════════════════════════════════════════

/** Send a single test email (row 2 only). Change the row to test a specific org. */
function sendTest() {
  BATCH_START = 2;
  BATCH_SIZE  = 1;
  sendBatch();
}

/** Send batch 1: rows 2–51 */
function sendBatch1() {
  BATCH_START = 2;
  BATCH_SIZE  = 50;
  sendBatch();
}

/** Send batch 2: rows 52–101 */
function sendBatch2() {
  BATCH_START = 52;
  BATCH_SIZE  = 50;
  sendBatch();
}

// ═══════════════════════════════════════════════════
// MENU — appears when the sheet opens
// ═══════════════════════════════════════════════════

function onOpen() {
  var ui = SpreadsheetApp.getUi();
  ui.createMenu("#SpeakingMyName")
    .addItem("🔍 Preview Batch",   "previewBatch")
    .addSeparator()
    .addItem("✉️  Send Test (1 email)",       "sendTest")
    .addItem("📬 Send Batch 1 (rows 2–51)",    "sendBatch1")
    .addItem("📬 Send Batch 2 (rows 52–101)",   "sendBatch2")
    .addSeparator()
    .addItem("⚙️  Send Custom Batch", "sendBatch")
    .addSeparator()
    .addItem("🔄 Sync Tracking", "syncTracking")
    .addItem("📊 Sync Dashboard", "syncDashboard")
    .addToUi();
}
