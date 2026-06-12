/**
 * SpeakHire Soiree Outreach Email Sender
 *
 * Sends drafted emails from the "Soiree Outreach" tab.
 * Sends all rows where Status = "DRAFTED" (no manual approval step).
 * Skips rows already marked as SENT.
 *
 * SETUP:
 * 1. Paste this into Extensions > Apps Script in your Google Sheet
 * 2. Run sendBatch() or use the Soiree Outreach menu
 */

// ═══════════════════════════════════════════════════
// CONFIG
// ═══════════════════════════════════════════════════

var BATCH_START = 2;
var BATCH_SIZE  = 50;
var SHEET_NAME  = "Soiree Outreach";
var DEFAULT_SENDER_NAME = "Hetal Jani from SpeakHire";

// ═══════════════════════════════════════════════════
// EMAIL SIGNATURE IMAGE
// Uses cid: inline attachment — the only method Gmail supports
// ═══════════════════════════════════════════════════

var SIGNATURE_IMAGE_ID = "1B77GL5DCAFMhIuzmsOpQpW2T1ixyLmgs";
var SIGNATURE_BLOB = null;

function getSignatureBlob() {
  if (SIGNATURE_BLOB) return SIGNATURE_BLOB;
  try {
    var file = DriveApp.getFileById(SIGNATURE_IMAGE_ID);
    var blob = file.getBlob();
    blob.setName("signature");
    Logger.log("OK signature: " + file.getName() + " | " + blob.getContentType() + " | " + blob.getBytes().length + " bytes");
    SIGNATURE_BLOB = blob;
    return blob;
  } catch (e) {
    Logger.log("FAIL signature: " + e.toString());
    return null;
  }
}

function getSignatureHtml() {
  return (
    '<br><br>' +
    '<img src="cid:signature" alt="SpeakHire"' +
    ' style="max-width:400px;width:100%;height:auto;border:none;" />'
  );
}

// ═══════════════════════════════════════════════════
// EMAIL TRACKING — Azure Functions (open + click)
// ═══════════════════════════════════════════════════

var TRACKING_BASE_URL = "https://YOUR_FUNCTION.azurewebsites.net";
var TRACKING_API_KEY  = "your-secret-key";        // same as Azure TRACKING_API_KEY
var CAMPAIGN_SLUG     = "soiree";

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

// Column mapping (1-based: A=1, ..., L=12)
var COL_NAME       = 1;   // A: Contact Name
var COL_TITLE      = 2;   // B: Job Title
var COL_ORG        = 3;   // C: Organization
var COL_EMAIL      = 4;   // D: Email
var COL_TYPE       = 5;   // E: Campaign Type
var COL_STATUS     = 6;   // F: Status
var COL_NOTES      = 7;   // G: Notes
var COL_SUBJECT    = 8;   // H: Email Subject
var COL_BODY       = 9;   // I: Personalized Email
var COL_RESEARCH   = 10;  // J: Research Notes
var COL_SENT_AT    = 11;  // K: Sent At

// ═══════════════════════════════════════════════════
// MAIN SEND FUNCTION
// ═══════════════════════════════════════════════════

function sendBatch() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  if (!sheet) {
    SpreadsheetApp.getUi().alert('Sheet "' + SHEET_NAME + '" not found.');
    return;
  }

  var lastRow = sheet.getLastRow();
  var data = sheet.getRange(1, 1, lastRow, COL_SENT_AT).getValues();

  var sent = 0, skipped = 0, errors = 0;
  var endRow = Math.min(BATCH_START + BATCH_SIZE - 1, lastRow);

  // Fetch signature image blob once
  var signatureBlob = getSignatureBlob();

  for (var i = BATCH_START - 1; i < endRow; i++) {
    var row = data[i];
    var status  = String(row[COL_STATUS - 1]  || "").trim();
    var email   = String(row[COL_EMAIL - 1]   || "").trim();
    var subject = String(row[COL_SUBJECT - 1] || "").trim();
    var body    = String(row[COL_BODY - 1]    || "").trim();
    var name    = String(row[COL_NAME - 1]    || "").trim();
    var orgName = String(row[COL_ORG - 1]     || "").trim();

    if (status === "SENT") { skipped++; continue; }
    if (status !== "DRAFTED") { continue; }
    if (!email || email.indexOf("@") === -1) { errors++; continue; }
    if (!body) { errors++; continue; }
    if (!subject) { subject = "SpeakHire Soiree — June 24 at Salesforce Tower"; }

    try {
      // Build basic HTML from plain text + tracking pixel
      var htmlBody = body
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\n/g, "<br>");
      htmlBody += getTrackingPixel(email, name, orgName, CAMPAIGN_SLUG);
      htmlBody += getSignatureHtml();
      htmlBody = '<div style="font-family:Arial,sans-serif;font-size:14px;color:#222;">' +
                 htmlBody + '</div>';

      var options = {
        htmlBody: htmlBody,
        name: DEFAULT_SENDER_NAME,
      };
      if (signatureBlob) {
        options.inlineImages = { signature: signatureBlob };
      }
      GmailApp.sendEmail(email, subject, body, options);
      sheet.getRange(i + 1, COL_STATUS).setValue("SENT");
      sheet.getRange(i + 1, COL_SENT_AT).setValue(new Date().toLocaleString());
      sent++;
      Logger.log("Sent: " + (orgName || name) + " -> " + email);
      Utilities.sleep(1000);
    } catch (e) {
      errors++;
      sheet.getRange(i + 1, COL_STATUS).setValue("ERROR");
      sheet.getRange(i + 1, COL_NOTES).setValue(
        (row[COL_NOTES - 1] || "") + " | SEND ERROR: " + e.toString().substring(0, 150));
      Logger.log("ERROR row " + (i + 1) + ": " + e);
    }
  }

  var msg = "Soiree batch complete!\n\nSent: " + sent +
    "\nSkipped: " + skipped + "\nErrors: " + errors +
    "\n\nScanned rows " + BATCH_START + " to " + endRow + " of " + lastRow;
  SpreadsheetApp.getUi().alert("Soiree Mail Merge", msg, SpreadsheetApp.getUi().ButtonSet.OK);
  Logger.log(msg);
}

function previewBatch() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  if (!sheet) { SpreadsheetApp.getUi().alert('Sheet not found.'); return; }
  var data = sheet.getRange(1, 1, sheet.getLastRow(), COL_SENT_AT).getValues();
  var wouldSend = 0, preview = [];
  var endRow = Math.min(BATCH_START + BATCH_SIZE - 1, sheet.getLastRow());

  for (var i = BATCH_START - 1; i < endRow; i++) {
    var row = data[i];
    if (String(row[COL_STATUS - 1] || "").trim() !== "DRAFTED") continue;
    if (!String(row[COL_EMAIL - 1] || "").trim()) continue;
    wouldSend++;
    preview.push("Row " + (i + 1) + " | " + (row[COL_ORG - 1] || row[COL_NAME - 1] || "?") +
                 " | " + (String(row[COL_SUBJECT - 1] || "").substring(0, 60)));
  }

  var msg = "DRY RUN — no emails sent.\n\nWould send: " + wouldSend + "\n\n" +
    (preview.length > 0 ? preview.slice(0, 15).join("\n") : "No approved emails found.");
  SpreadsheetApp.getUi().alert("Soiree Preview", msg, SpreadsheetApp.getUi().ButtonSet.OK);
}

function sendTest() { BATCH_START = 2; BATCH_SIZE = 1; sendBatch(); }
function sendBatch1() { BATCH_START = 2; BATCH_SIZE = 50; sendBatch(); }
function sendBatch2() { BATCH_START = 52; BATCH_SIZE = 50; sendBatch(); }

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("Soiree Outreach")
    .addItem("🔍 Preview Batch", "previewBatch")
    .addSeparator()
    .addItem("✉️  Send Test (1 email)", "sendTest")
    .addItem("📬 Send Batch 1 (rows 2–51)", "sendBatch1")
    .addItem("📬 Send Batch 2 (rows 52–101)", "sendBatch2")
    .addSeparator()
    .addItem("⚙️  Send Custom Batch", "sendBatch")
    .addSeparator()
    .addItem("🔄 Sync Tracking", "syncTracking")
    .addItem("📊 Sync Dashboard", "syncDashboard")
    .addToUi();
}
