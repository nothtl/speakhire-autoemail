/**
 * SpeakHire Summit Email Sender
 *
 * Sends personalized emails from the "Summit Outreach 2026" tab.
 * Change BATCH_START and BATCH_SIZE below to control which rows get sent.
 *
 * SETUP:
 * 1. Paste this into Extensions > Apps Script in your Google Sheet
 * 2. Replace DRIVE_FILE_ID below with your flyer's Google Drive file ID
 * 3. Replace DRAFT_SUBJECT with your draft subject line (for the inline image template)
 * 4. Run sendBatch() from the editor, or use the Mail Merge menu
 */

// ═══════════════════════════════════════════════════
// CONFIG — CHANGE THESE
// ═══════════════════════════════════════════════════

var BATCH_START = 2; // Row number to start from (row 2 = first contact)
var BATCH_SIZE = 100; // How many to send in this batch
var SHEET_NAME = "Summit Outreach 2026";

// Your flyer/image hosted on Google Drive (copy the file ID from the share link)
var DRIVE_FILE_ID =
  "https://docs.google.com/spreadsheets/d/1afKWetT_AEwqAS9nYieMfxWjEZk2zf3TQZ0mOWo0qCA/edit?usp=sharing";

// SpeakHire email signature image (Google Drive file ID)
var SIGNATURE_IMAGE_ID = "1B77GL5DCAFMhIuzmsOpQpW2T1ixyLmgs";

// The exact subject line of your Gmail draft (used to find the draft with inline image)
var DRAFT_SUBJECT = "SpeakHire Summit Invitation";

// ═══════════════════════════════════════════════════
// COLUMN MAPPING (1-based: A=1, B=2, etc.)
// ═══════════════════════════════════════════════════
var COL_FIRST_NAME = 1; // A
var COL_LAST_NAME = 2; // B
var COL_EMAIL = 3; // C
var COL_SUBJECT = 7; // G
var COL_COMBINED = 10; // J
var COL_STATUS = 11; // K

// ═══════════════════════════════════════════════════
// EMAIL TRACKING — Azure Functions (open + click)
// ═══════════════════════════════════════════════════

var TRACKING_BASE_URL = "https://YOUR_FUNCTION.azurewebsites.net";
var TRACKING_API_KEY  = "your-secret-key";        // same as Azure TRACKING_API_KEY
var CAMPAIGN_SLUG     = "summit";

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
// EMAIL IMAGES — Embedded as base64 data URLs
// No external URLs, no cid, no inlineImages needed
// ═══════════════════════════════════════════════════

var FLYER_DATA_URL = "";
var SIGNATURE_DATA_URL = "";

function buildDataUrl(fileId) {
  try {
    var file = DriveApp.getFileById(fileId);
    var blob = file.getBlob();
    var mimeType = blob.getContentType() || "image/png";
    var base64 = Utilities.base64Encode(blob.getBytes());
    return "data:" + mimeType + ";base64," + base64;
  } catch (e) {
    return "";
  }
}

/**
 * Build the HTML body for an email.
 * Images are embedded as base64 data URLs — works in all email clients.
 */
function buildHtmlBody(plainText, firstName) {
  // Replace any {{First Name}} placeholders
  var body = plainText.replace(/\{\{First Name\}\}/g, firstName);
  body = body.replace(/\{\{First Name\}\}/gi, firstName);

  // Convert plain text to basic HTML (line breaks -> <br>)
  var htmlBody = body
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\n/g, "<br>");

  // Add the flyer image
  if (FLYER_DATA_URL) {
    htmlBody +=
      '<br><br><img src="' + FLYER_DATA_URL + '"' +
      ' style="max-width:100%;height:auto;display:block;"' +
      ' alt="SpeakHire Summit flyer">';
  }

  // Add the email signature image
  if (SIGNATURE_DATA_URL) {
    htmlBody +=
      '<br><br><img src="' + SIGNATURE_DATA_URL + '"' +
      ' style="max-width:400px;width:100%;height:auto;border:none;"' +
      ' alt="SpeakHire">';
  }

  return (
    '<div style="font-family:Arial,sans-serif;font-size:14px;color:#222;">' +
    htmlBody +
    "</div>"
  );
}

/**
 * Main function — sends batch of emails.
 * Run this from the Apps Script editor or hook it to a menu.
 */
function sendBatch() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  if (!sheet) {
    SpreadsheetApp.getUi().alert('Sheet "' + SHEET_NAME + '" not found.');
    return;
  }

  // Get all data
  var lastRow = sheet.getLastRow();
  var data = sheet.getRange(1, 1, lastRow, COL_STATUS).getValues();

  var sent = 0;
  var skipped = 0;
  var errors = 0;
  var endRow = Math.min(BATCH_START + BATCH_SIZE - 1, lastRow);

  // Pre-build image data URLs once
  FLYER_DATA_URL = buildDataUrl(DRIVE_FILE_ID);
  SIGNATURE_DATA_URL = buildDataUrl(SIGNATURE_IMAGE_ID);

  for (var i = BATCH_START - 1; i < endRow; i++) {
    var row = data[i];
    var email = String(row[COL_EMAIL - 1] || "").trim();
    var subject = String(row[COL_SUBJECT - 1] || "").trim();
    var combined = String(row[COL_COMBINED - 1] || "").trim();
    var status = String(row[COL_STATUS - 1] || "").trim();
    var firstName = String(row[COL_FIRST_NAME - 1] || "").trim();

    // Skip if already sent
    if (status === "Sent") {
      skipped++;
      continue;
    }

    // Skip if no email or no content
    if (!email || !combined) {
      continue;
    }

    // Skip if no subject
    if (!subject) {
      errors++;
      sheet.getRange(i + 1, COL_STATUS).setValue("Error: No subject");
      continue;
    }

    try {
      var htmlBody = buildHtmlBody(combined, firstName);

      // Append tracking pixel
      htmlBody += getTrackingPixel(email, firstName, "Summit Attendee", CAMPAIGN_SLUG);

      GmailApp.sendEmail(email, subject, combined, {
        htmlBody: htmlBody,
        name: "Alicia Zhuang from SpeakHire",
      });

      // Mark as sent
      var timestamp = new Date().toLocaleString();
      sheet.getRange(i + 1, COL_STATUS).setValue("Sent " + timestamp);
      sent++;

      // Rate limit protection: max 100 emails per ~16 min for free Gmail accounts
      // Pause 1 second between sends to stay safe
      Utilities.sleep(1000);
    } catch (e) {
      errors++;
      sheet.getRange(i + 1, COL_STATUS).setValue("Error: " + e.toString());
    }
  }

  // Show summary
  var msg =
    "Batch complete!\n\n" +
    "Sent: " +
    sent +
    "\n" +
    "Skipped (already sent): " +
    skipped +
    "\n" +
    "Errors: " +
    errors +
    "\n\n" +
    "Rows " +
    BATCH_START +
    " to " +
    endRow +
    " of " +
    lastRow;

  SpreadsheetApp.getUi().alert(
    "Mail Merge Done",
    msg,
    SpreadsheetApp.getUi().ButtonSet.OK,
  );

  // Auto-advance BATCH_START for next run
  // Uncomment below to auto-advance (saves having to change the code manually):
  // PropertiesService.getScriptProperties().setProperty('NEXT_BATCH', String(endRow + 1));
}

/**
 * Send batch 1 (rows 2-101).
 */
function sendBatch1() {
  BATCH_START = 2;
  BATCH_SIZE = 100;
  sendBatch();
}

/**
 * Send batch 2 (rows 102-200).
 */
function sendBatch2() {
  BATCH_START = 102;
  BATCH_SIZE = 100;
  sendBatch();
}

/**
 * Send only the test row (row 2 = Ephraim). Change the row number to test.
 */
function sendTest() {
  BATCH_START = 2; // Row 2 = Tingli (test yourself first!)
  BATCH_SIZE = 1;
  sendBatch();
}

/**
 * Create the Mail Merge menu when the sheet opens.
 */
function onOpen() {
  var ui = SpreadsheetApp.getUi();
  ui.createMenu("Mail Merge")
    .addItem("Send Test (1 email)", "sendTest")
    .addItem("Send Batch 1 (rows 2-101)", "sendBatch1")
    .addItem("Send Batch 2 (rows 102-200)", "sendBatch2")
    .addSeparator()
    .addItem("Send Custom Batch", "sendBatch")
    .addSeparator()
    .addItem("🔄 Sync Tracking", "syncTracking")
    .addItem("📊 Sync Dashboard", "syncDashboard")
    .addToUi();
}
