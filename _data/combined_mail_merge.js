/**
 * SpeakHire Combined Mail Merge
 *
 * Handles BOTH outreach campaigns from one Apps Script project:
 *   - Summit Outreach 2026    (tab: "Summit Outreach 2026")
 *   - #SpeakingMyName Outreach (tab: "#SpeakingMyName Outreach")
 *
 * Paste this entire file into Extensions > Apps Script.
 * Two menus will appear: "Summit" and "#SpeakingMyName"
 *
 * Each campaign has its own column mapping, sender name, and tab.
 * Only rows marked APPROVED get sent. Status changes to SENT + timestamp.
 */

// ═══════════════════════════════════════════════════
// MENU — creates both campaign menus on open
// ═══════════════════════════════════════════════════

function onOpen() {
  var ui = SpreadsheetApp.getUi();

  // Summit menu
  ui.createMenu("Summit Outreach")
    .addItem("✉️  Send Test (1 email)",   "summitSendTest")
    .addItem("📬 Send Batch 1 (rows 2–101)", "summitSendBatch1")
    .addItem("📬 Send Batch 2 (rows 102–200)", "summitSendBatch2")
    .addSeparator()
    .addItem("⚙️  Send Custom Batch", "summitSendBatch")
    .addToUi();

  // #SpeakingMyName menu
  ui.createMenu("#SpeakingMyName")
    .addItem("🔍 Preview Batch",    "smnPreviewBatch")
    .addSeparator()
    .addItem("✉️  Send Test (1 email)",  "smnSendTest")
    .addItem("📬 Send Batch 1 (rows 2–51)", "smnSendBatch1")
    .addItem("📬 Send Batch 2 (rows 52–101)", "smnSendBatch2")
    .addSeparator()
    .addItem("⚙️  Send Custom Batch", "smnSendBatch")
    .addToUi();
}

// ═══════════════════════════════════════════════════
// SUMMIT OUTREACH 2026
// ═══════════════════════════════════════════════════

var SUMMIT_SHEET = "Summit Outreach 2026";
var SUMMIT_SENDER = "Alicia Zhuang from SpeakHire";
var SUMMIT_COL_FIRST  = 1;  // A
var SUMMIT_COL_LAST   = 2;  // B
var SUMMIT_COL_EMAIL  = 3;  // C
var SUMMIT_COL_SUBJECT = 7; // G
var SUMMIT_COL_COMBINED = 10; // J
var SUMMIT_COL_STATUS  = 11; // K

function summitSendBatch() {
  var BATCH_START = 2, BATCH_SIZE = 100;
  _sendBatch(SUMMIT_SHEET, SUMMIT_COL_EMAIL, SUMMIT_COL_SUBJECT, SUMMIT_COL_COMBINED,
             SUMMIT_COL_STATUS, null, SUMMIT_SENDER, SUMMIT_COL_FIRST, BATCH_START, BATCH_SIZE);
}

function summitSendBatch1() { var BATCH_START = 2, BATCH_SIZE = 100; _sendBatch(SUMMIT_SHEET, SUMMIT_COL_EMAIL, SUMMIT_COL_SUBJECT, SUMMIT_COL_COMBINED, SUMMIT_COL_STATUS, null, SUMMIT_SENDER, SUMMIT_COL_FIRST, BATCH_START, BATCH_SIZE); }
function summitSendBatch2() { var BATCH_START = 102, BATCH_SIZE = 100; _sendBatch(SUMMIT_SHEET, SUMMIT_COL_EMAIL, SUMMIT_COL_SUBJECT, SUMMIT_COL_COMBINED, SUMMIT_COL_STATUS, null, SUMMIT_SENDER, SUMMIT_COL_FIRST, BATCH_START, BATCH_SIZE); }
function summitSendTest()   { var BATCH_START = 2, BATCH_SIZE = 1;   _sendBatch(SUMMIT_SHEET, SUMMIT_COL_EMAIL, SUMMIT_COL_SUBJECT, SUMMIT_COL_COMBINED, SUMMIT_COL_STATUS, null, SUMMIT_SENDER, SUMMIT_COL_FIRST, BATCH_START, BATCH_SIZE); }

// ═══════════════════════════════════════════════════
// #SPEAKINGMYNAME OUTREACH
// ═══════════════════════════════════════════════════

var SMN_SHEET = "#SpeakingMyName Outreach";
var SMN_SENDER = "Hana Figueroa from SpeakHire";
var SMN_COL_NAME     = 1;   // A
var SMN_COL_TITLE    = 2;   // B
var SMN_COL_ASSOC    = 3;   // C
var SMN_COL_EMAIL    = 5;   // E
var SMN_COL_STATUS   = 6;   // F
var SMN_COL_NOTES    = 7;   // G
var SMN_COL_SUBJECT  = 8;   // H
var SMN_COL_BODY     = 9;   // I
var SMN_COL_SENT_AT  = 11;  // K

function smnSendBatch()  { var BATCH_START = 2, BATCH_SIZE = 50; _sendBatch(SMN_SHEET, SMN_COL_EMAIL, SMN_COL_SUBJECT, SMN_COL_BODY, SMN_COL_STATUS, SMN_COL_SENT_AT, SMN_SENDER, null, BATCH_START, BATCH_SIZE); }
function smnSendBatch1() { var BATCH_START = 2, BATCH_SIZE = 50; _sendBatch(SMN_SHEET, SMN_COL_EMAIL, SMN_COL_SUBJECT, SMN_COL_BODY, SMN_COL_STATUS, SMN_COL_SENT_AT, SMN_SENDER, null, BATCH_START, BATCH_SIZE); }
function smnSendBatch2() { var BATCH_START = 52, BATCH_SIZE = 50; _sendBatch(SMN_SHEET, SMN_COL_EMAIL, SMN_COL_SUBJECT, SMN_COL_BODY, SMN_COL_STATUS, SMN_COL_SENT_AT, SMN_SENDER, null, BATCH_START, BATCH_SIZE); }
function smnSendTest()   { var BATCH_START = 2, BATCH_SIZE = 1;  _sendBatch(SMN_SHEET, SMN_COL_EMAIL, SMN_COL_SUBJECT, SMN_COL_BODY, SMN_COL_STATUS, SMN_COL_SENT_AT, SMN_SENDER, null, BATCH_START, BATCH_SIZE); }

function smnPreviewBatch() {
  var BATCH_START = 2, BATCH_SIZE = 50;
  _previewBatch(SMN_SHEET, SMN_COL_EMAIL, SMN_COL_SUBJECT, SMN_COL_BODY, SMN_COL_STATUS, SMN_COL_ASSOC, BATCH_START, BATCH_SIZE);
}

// ═══════════════════════════════════════════════════
// SHARED SEND ENGINE — used by both campaigns
// ═══════════════════════════════════════════════════

/**
 * Generic batch sender. Parameters:
 *   sheetName  - tab name
 *   colEmail, colSubject, colBody, colStatus - column indices (1-based)
 *   colSentAt  - column for timestamp (null = use colStatus to also write "Sent DATE")
 *   senderName - display name for GmailApp
 *   colName    - column with first name for {{First Name}} replacement (Summit only, null for SMN)
 *   batchStart, batchSize
 */
function _sendBatch(sheetName, colEmail, colSubject, colBody, colStatus, colSentAt,
                    senderName, colName, batchStart, batchSize) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(sheetName);
  if (!sheet) {
    SpreadsheetApp.getUi().alert('Sheet "' + sheetName + '" not found.');
    return;
  }

  var lastRow = sheet.getLastRow();
  var lastCol = Math.max(colEmail, colSubject, colBody, colStatus, colSentAt || 0);
  var data = sheet.getRange(1, 1, lastRow, lastCol).getValues();

  var sent = 0, skipped = 0, errors = 0;
  var endRow = Math.min(batchStart + batchSize - 1, lastRow);

  for (var i = batchStart - 1; i < endRow; i++) {
    var row = data[i];
    var status  = String(row[colStatus - 1]  || "").trim();
    var email   = String(row[colEmail - 1]   || "").trim();
    var subject = String(row[colSubject - 1] || "").trim();
    var body    = String(row[colBody - 1]    || "").trim();

    // Skip already sent
    if (status === "SENT" || status.indexOf("Sent") === 0) { skipped++; continue; }
    // Summit: send any row with content (not error/empty). SMN: only APPROVED
    if (sheetName !== SUMMIT_SHEET && status !== "APPROVED") { continue; }
    if (!email || email.indexOf("@") === -1) { errors++; continue; }
    if (!body) { errors++; continue; }

    // Summit-specific: replace {{First Name}} in body
    if (colName) {
      var firstName = String(row[colName - 1] || "").trim();
      body = body.replace(/\{\{First Name\}\}/g, firstName);
      body = body.replace(/\{\{First Name\}\}/gi, firstName);
    }

    if (!subject) { subject = "SpeakHire"; }

    try {
      GmailApp.sendEmail(email, subject, body, { name: senderName });
      var timestamp = new Date().toLocaleString();
      sheet.getRange(i + 1, colStatus).setValue("Sent " + timestamp);
      if (colSentAt) {
        sheet.getRange(i + 1, colSentAt).setValue(timestamp);
      }
      sent++;
      Logger.log("Sent to " + email + " — " + subject);
      Utilities.sleep(1000);
    } catch (e) {
      errors++;
      sheet.getRange(i + 1, colStatus).setValue("Error: " + e.toString().substring(0, 180));
      Logger.log("ERROR row " + (i + 1) + ": " + e);
    }
  }

  var msg = sheetName + " batch complete!\n\n" +
    "Sent: " + sent + "\nSkipped: " + skipped + "\nErrors: " + errors + "\n\n" +
    "Scanned rows " + batchStart + " to " + endRow + " of " + lastRow;
  SpreadsheetApp.getUi().alert("Mail Merge Done", msg, SpreadsheetApp.getUi().ButtonSet.OK);
  Logger.log(msg);
}

/**
 * Preview / dry run for SMN campaign.
 */
function _previewBatch(sheetName, colEmail, colSubject, colBody, colStatus, colAssoc,
                        batchStart, batchSize) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(sheetName);
  if (!sheet) { SpreadsheetApp.getUi().alert('Sheet not found.'); return; }

  var lastRow = sheet.getLastRow();
  var lastCol = Math.max(colEmail, colSubject, colBody, colStatus, colAssoc);
  var data = sheet.getRange(1, 1, lastRow, lastCol).getValues();

  var wouldSend = 0, preview = [];
  var endRow = Math.min(batchStart + batchSize - 1, lastRow);

  for (var i = batchStart - 1; i < endRow; i++) {
    var row = data[i];
    var status = String(row[colStatus - 1] || "").trim();
    var email  = String(row[colEmail - 1]  || "").trim();
    var body   = String(row[colBody - 1]   || "").trim();
    if (status === "SENT" || status.indexOf("Sent") === 0) continue;
    if (status !== "APPROVED") continue;
    if (!email || email.indexOf("@") === -1) continue;
    if (!body) continue;
    wouldSend++;
    preview.push("Row " + (i + 1) + " | " + (row[colAssoc - 1] || "?") +
                 " | " + email + " | " + String(row[colSubject - 1] || "").substring(0, 50));
  }

  var msg = "DRY RUN — no emails sent.\n\nWould send: " + wouldSend + "\n\n" +
    (preview.length ? preview.slice(0, 15).join("\n") : "No approved emails found.");
  SpreadsheetApp.getUi().alert(sheetName + " Preview", msg, SpreadsheetApp.getUi().ButtonSet.OK);
}
