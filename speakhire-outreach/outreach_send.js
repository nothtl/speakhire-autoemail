/**
 * SpeakHire Outreach Email Sender
 *
 * Sends drafted sponsorship/partnership emails from the "Outreach Tracker" tab.
 * Sends all rows where STATUS = "DRAFTED" (no manual approval step).
 * Skips rows with OPT_OUT = "TRUE".
 * Uses HUMAN_EDITED_DRAFT (column J) if available, otherwise EMAIL_DRAFT (column I).
 *
 * SETUP:
 * 1. Paste this into Extensions > Apps Script in your Google Sheet
 * 2. Adjust BATCH_START / BATCH_SIZE below
 * 3. Run sendBatch() from the editor, or use the SpeakHire Mail Merge menu
 *
 * DIFFERENCES FROM THE SUMMIT SCRIPT:
 * - Works with "Outreach Tracker" tab (37-column schema)
 * - No inline image — these are plain-text sponsorship emails
 * - Sender name comes from the SENDER_NAME column (or defaults)
 * - Only sends APPOVED rows (not all unsent rows)
 */

// ═══════════════════════════════════════════════════
// CONFIG — CHANGE THESE
// ═══════════════════════════════════════════════════

var BATCH_START = 2;      // Row number to start from (row 2 = first contact)
var BATCH_SIZE  = 50;     // How many rows to scan in this batch
var SHEET_NAME  = "Outreach Tracker";

// Default sender name (used when SENDER_NAME column is empty)
var DEFAULT_SENDER_NAME = "Hana from SpeakHire";

// ═══════════════════════════════════════════════════
// EMAIL TRACKING — Azure Functions (open + click)
// ═══════════════════════════════════════════════════

var TRACKING_BASE_URL = "https://YOUR_FUNCTION.azurewebsites.net";
var CAMPAIGN_SLUG     = "general";

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

function getTrackingPixel(email, name, orgName, campaign) {
  var id = encodeTrackingId(email, name, orgName, campaign);
  return '<img src="' + TRACKING_BASE_URL + '/api/o/' + id + '"' +
    ' width="1" height="1" alt=""' +
    ' style="display:none !important;visibility:hidden !important;' +
    'opacity:0 !important;width:1px !important;height:1px !important;" />';
}

// ═══════════════════════════════════════════════════
// COLUMN MAPPING (1-based: A=1, B=2, ..., AK=37)
// ═══════════════════════════════════════════════════
var COL_ORG_NAME       = 1;   // A
var COL_ORG_WEBSITE    = 2;   // B
var COL_RECIPIENT      = 3;   // C
var COL_EMAIL          = 4;   // D
var COL_STATUS         = 5;   // E
var COL_NOTES          = 6;   // F
var COL_PERSONALISED   = 7;   // G
var COL_EMAIL_SUBJECT  = 8;   // H
var COL_EMAIL_DRAFT    = 9;   // I
var COL_HUMAN_EDITED   = 10;  // J
var COL_SENDER_NAME    = 11;  // K
var COL_OPT_OUT        = 12;  // L
var COL_SENT_AT        = 13;  // M
var COL_ERROR          = 14;  // N
var COL_CTA_TYPE       = 29;  // AC
var COL_CALL_DURATION  = 30;  // AD
var COL_SENDER_TITLE   = 31;  // AE
var COL_SENDER_ORG     = 32;  // AF
var COL_SEND_FROM      = 33;  // AG
var COL_EMAIL_PROVIDER = 34;  // AH

/**
 * Determine what email body to use.
 * Priority: HUMAN_EDITED_DRAFT > EMAIL_DRAFT (never send empty body).
 */
function getEmailBody(humanEdited, aiDraft) {
  var body = (humanEdited || "").trim();
  if (body) return body;
  return (aiDraft || "").trim();
}

/**
 * Determine the sender display name.
 * Priority: SEND_FROM > SENDER_NAME > sheet default > hardcoded default.
 */
function getSenderName(senderName, sendFrom) {
  var name = (sendFrom || "").trim();
  if (name) return name;
  name = (senderName || "").trim();
  if (name) return name + " from SpeakHire";
  return DEFAULT_SENDER_NAME;
}

/**
 * Build a plain-text email with a standard signature block.
 * The AI drafts already include a signature, but we add SpeakHire
 * branding if it's missing.
 */
function buildEmailBody(body, senderName) {
  // Strip any trailing whitespace but keep the structure
  var cleanBody = body.trim();

  // If the body already has a sign-off ("Best," / "Sincerely," / "Warmly,"),
  // leave it as-is. Otherwise append the SpeakHire signature.
  var hasSignOff =
    /\b(Best,?|Sincerely,?|Warmly,?|Cheers,?|Thank you,?|Thanks,?)\s*$/m.test(
      cleanBody
    );

  if (!hasSignOff) {
    // Check if it ends with a name that looks like the sender
    if (!/\b(Hana|Alicia|Tingli)\b/.test(cleanBody.slice(-40))) {
      cleanBody +=
        "\n\nBest,\n" +
        senderName.replace(" from SpeakHire", "") +
        "\nSpeakHire";
    }
  }

  return cleanBody;
}

// ═══════════════════════════════════════════════════
// MAIN SEND FUNCTION
// ═══════════════════════════════════════════════════

function sendBatch() {
  var sheet =
    SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  if (!sheet) {
    SpreadsheetApp.getUi().alert(
      'Sheet "' + SHEET_NAME + '" not found. ' +
      'Make sure the "Outreach Tracker" tab exists.'
    );
    return;
  }

  // Read the full sheet up to COL_EMAIL_PROVIDER (widest column we need)
  var lastRow = sheet.getLastRow();
  var lastCol = COL_EMAIL_PROVIDER;
  var data = sheet.getRange(1, 1, lastRow, lastCol).getValues();

  var sent    = 0;
  var skipped = 0;
  var errors  = 0;
  var endRow  = Math.min(BATCH_START + BATCH_SIZE - 1, lastRow);

  for (var i = BATCH_START - 1; i < endRow; i++) {
    var row = data[i];

    var status     = String(row[COL_STATUS - 1]       || "").trim();
    var email      = String(row[COL_EMAIL - 1]         || "").trim();
    var subject    = String(row[COL_EMAIL_SUBJECT - 1] || "").trim();
    var aiDraft    = String(row[COL_EMAIL_DRAFT - 1]   || "").trim();
    var humanEdit  = String(row[COL_HUMAN_EDITED - 1]  || "").trim();
    var senderName = String(row[COL_SENDER_NAME - 1]   || "").trim();
    var sendFrom   = String(row[COL_SEND_FROM - 1]     || "").trim();
    var optOut     = String(row[COL_OPT_OUT - 1]       || "").trim();
    var orgName    = String(row[COL_ORG_NAME - 1]      || "").trim();
    var recipient  = String(row[COL_RECIPIENT - 1]     || "").trim();

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

    // Opt-out
    if (optOut.toUpperCase() === "TRUE" || optOut === "1") {
      skipped++;
      continue;
    }

    // No email address
    if (!email) {
      errors++;
      sheet.getRange(i + 1, COL_STATUS).setValue("ERROR");
      sheet.getRange(i + 1, COL_ERROR).setValue("No EMAIL address");
      continue;
    }

    // No body to send
    var body = getEmailBody(humanEdit, aiDraft);
    if (!body) {
      errors++;
      sheet.getRange(i + 1, COL_STATUS).setValue("ERROR");
      sheet.getRange(i + 1, COL_ERROR).setValue("No EMAIL_DRAFT or HUMAN_EDITED_DRAFT");
      continue;
    }

    // No subject
    if (!subject) {
      subject = "#SpeakingMyName partnership with SpeakHire";
    }

    // --- SEND ---
    try {
      var displayName = getSenderName(senderName, sendFrom);
      var finalBody = buildEmailBody(body, displayName);

      // Build HTML from plain text + tracking pixel
      var htmlBody = finalBody
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\n/g, "<br>");
      htmlBody += getTrackingPixel(email, recipient, orgName, CAMPAIGN_SLUG);
      htmlBody = '<div style="font-family:Arial,sans-serif;font-size:14px;color:#222;">' +
                 htmlBody + '</div>';

      GmailApp.sendEmail(email, subject, finalBody, {
        htmlBody: htmlBody,
        name: displayName,
      });

      // Mark as sent with timestamp
      var timestamp = new Date().toLocaleString();
      sheet.getRange(i + 1, COL_STATUS).setValue("SENT");
      sheet.getRange(i + 1, COL_SENT_AT).setValue(timestamp);
      sheet.getRange(i + 1, COL_EMAIL_PROVIDER).setValue("gmail_ok");
      sheet.getRange(i + 1, COL_ERROR).setValue("");
      sent++;

      Logger.log(
        "Sent to " + email + " (" + orgName + ") — " + subject
      );

      // Rate limit: Gmail free accounts allow ~100 emails/day
      // Sleep 1 second between sends to stay safe
      Utilities.sleep(1000);
    } catch (e) {
      errors++;
      var errMsg = e.toString().substring(0, 200);
      sheet.getRange(i + 1, COL_STATUS).setValue("ERROR");
      sheet.getRange(i + 1, COL_ERROR).setValue(errMsg);
      sheet.getRange(i + 1, COL_EMAIL_PROVIDER).setValue("gmail_fail");
      Logger.log("ERROR row " + (i + 1) + ": " + errMsg);
    }
  }

  // --- SUMMARY ---
  var msg =
    "Outreach batch complete!\n\n" +
    "Sent: "             + sent    + "\n" +
    "Skipped (not DRAFTED / already SENT): " + skipped + "\n" +
    "Errors: "           + errors  + "\n\n" +
    "Scanned rows "      + BATCH_START + " to " + endRow +
    " of " + lastRow + " in '" + SHEET_NAME + "'";

  SpreadsheetApp.getUi().alert("Mail Merge Done", msg, SpreadsheetApp.getUi().ButtonSet.OK);
  Logger.log(msg);
}

// ═══════════════════════════════════════════════════
// CONVENIENCE BATCH FUNCTIONS
// ═══════════════════════════════════════════════════

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

/** Send batch 3: rows 102–151 */
function sendBatch3() {
  BATCH_START = 102;
  BATCH_SIZE  = 50;
  sendBatch();
}

/** Send a single test email (row 2 only). Change the row number to test. */
function sendTest() {
  BATCH_START = 2;
  BATCH_SIZE  = 1;
  sendBatch();
}

// ═══════════════════════════════════════════════════
// DRY RUN — preview what WOULD be sent without actually sending
// ═══════════════════════════════════════════════════

function previewBatch() {
  var sheet =
    SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  if (!sheet) {
    SpreadsheetApp.getUi().alert(
      'Sheet "' + SHEET_NAME + '" not found.'
    );
    return;
  }

  var lastRow = sheet.getLastRow();
  var lastCol = COL_EMAIL_PROVIDER;
  var data = sheet.getRange(1, 1, lastRow, lastCol).getValues();

  var wouldSend  = 0;
  var wouldSkip  = 0;
  var preview    = [];
  var endRow     = Math.min(BATCH_START + BATCH_SIZE - 1, lastRow);

  for (var i = BATCH_START - 1; i < endRow; i++) {
    var row = data[i];

    var status     = String(row[COL_STATUS - 1]       || "").trim();
    var email      = String(row[COL_EMAIL - 1]         || "").trim();
    var subject    = String(row[COL_EMAIL_SUBJECT - 1] || "").trim();
    var aiDraft    = String(row[COL_EMAIL_DRAFT - 1]   || "").trim();
    var humanEdit  = String(row[COL_HUMAN_EDITED - 1]  || "").trim();
    var senderName = String(row[COL_SENDER_NAME - 1]   || "").trim();
    var sendFrom   = String(row[COL_SEND_FROM - 1]     || "").trim();
    var optOut     = String(row[COL_OPT_OUT - 1]       || "").trim();
    var orgName    = String(row[COL_ORG_NAME - 1]      || "").trim();

    // Same skip logic as sendBatch
    if (status === "SENT") { wouldSkip++; continue; }
    if (status !== "DRAFTED") { continue; }
    if (optOut.toUpperCase() === "TRUE" || optOut === "1") { wouldSkip++; continue; }
    if (!email) { continue; }

    var body = getEmailBody(humanEdit, aiDraft);
    if (!body) { continue; }

    var displayName = getSenderName(senderName, sendFrom);

    wouldSend++;
    preview.push(
      "Row " + (i + 1) + " | " + orgName +
      " | To: " + email +
      " | From: " + displayName +
      " | Subject: " + (subject || "(default)").substring(0, 60)
    );
  }

  var msg =
    "DRY RUN — no emails were sent.\n\n" +
    "Would send:  " + wouldSend + "\n" +
    "Would skip:  " + wouldSkip + "\n\n" +
    (preview.length > 0
      ? "Preview (first " + Math.min(20, preview.length) + "):\n" +
        preview.slice(0, 20).join("\n") +
        (preview.length > 20
          ? "\n... and " + (preview.length - 20) + " more"
          : "")
      : "No approved emails found in rows " +
        BATCH_START + "–" + endRow);

  SpreadsheetApp.getUi().alert(
    "Mail Merge Preview",
    msg,
    SpreadsheetApp.getUi().ButtonSet.OK
  );
  Logger.log(msg);
}

// ═══════════════════════════════════════════════════
// MENU (appears when the sheet opens)
// ═══════════════════════════════════════════════════

function onOpen() {
  var ui = SpreadsheetApp.getUi();
  ui.createMenu("SpeakHire Outreach")
    .addItem("🔍 Preview Batch",   "previewBatch")
    .addSeparator()
    .addItem("✉️  Send Test (1 email)",      "sendTest")
    .addItem("📬 Send Batch 1 (rows 2–51)",   "sendBatch1")
    .addItem("📬 Send Batch 2 (rows 52–101)",  "sendBatch2")
    .addItem("📬 Send Batch 3 (rows 102–151)", "sendBatch3")
    .addSeparator()
    .addItem("⚙️  Send Custom Batch", "sendBatch")
    .addSeparator()
    .addItem("🔄 Sync Tracking", "syncTracking")
    .addItem("📊 Sync Dashboard", "syncDashboard")
    .addToUi();
}
