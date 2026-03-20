const PptxGenJS = require("pptxgenjs");

const pres = new PptxGenJS();

const slide = pres.addSlide();
slide.background = { color: "0F1419" }; // Deep charcoal

// ═══════════════════════════════════════════════════════════════
// TITLE
// ═══════════════════════════════════════════════════════════════
slide.addText("DealRadar", {
  x: 0.5, y: 0.4, w: 9, h: 0.6,
  fontSize: 54, bold: true, color: "FFFFFF",
  fontFace: "Arial",
});

slide.addText("AI-Powered Car Deal Intelligence", {
  x: 0.5, y: 1.0, w: 9, h: 0.4,
  fontSize: 24, color: "00D9FF",
  fontFace: "Arial",
});

// ═══════════════════════════════════════════════════════════════
// FLOW SECTION
// ═══════════════════════════════════════════════════════════════

// USER INPUT (left)
const userX = 0.8, userY = 1.8;
slide.addShape(pres.ShapeType.roundRect, {
  x: userX, y: userY, w: 1.5, h: 1.2,
  fill: { color: "1E88E5" },
  line: { color: "00D9FF", width: 2 },
  rectRadius: 0.2,
});
slide.addText("User\nQuery", {
  x: userX, y: userY + 0.25, w: 1.5, h: 0.7,
  fontSize: 16, bold: true, color: "FFFFFF", align: "center",
  fontFace: "Arial",
});

// ARROW 1
slide.addText("→", {
  x: userX + 1.4, y: userY + 0.35, w: 0.8, h: 0.5,
  fontSize: 24, color: "00D9FF", align: "center",
});

// ═══════════════════════════════════════════════════════════════
// AI AGENTS (center cluster)
// ═══════════════════════════════════════════════════════════════

const agentsX = 3.2, agentsY = 1.6;
const agentSize = 1.1;
const agentGap = 1.4;

// Agent 1 (top-left)
slide.addShape(pres.ShapeType.ellipse, {
  x: agentsX, y: agentsY, w: agentSize, h: agentSize,
  fill: { color: "FF6B6B" },
  line: { color: "FFFFFF", width: 2 },
});
slide.addText("Intent\nExtractor", {
  x: agentsX, y: agentsY + 0.2, w: agentSize, h: 0.7,
  fontSize: 12, bold: true, color: "FFFFFF", align: "center",
  fontFace: "Arial",
});

// Agent 2 (top-right)
slide.addShape(pres.ShapeType.ellipse, {
  x: agentsX + agentGap, y: agentsY, w: agentSize, h: agentSize,
  fill: { color: "4ECDC4" },
  line: { color: "FFFFFF", width: 2 },
});
slide.addText("Deal\nAnalyst", {
  x: agentsX + agentGap, y: agentsY + 0.2, w: agentSize, h: 0.7,
  fontSize: 12, bold: true, color: "FFFFFF", align: "center",
  fontFace: "Arial",
});

// Agent 3 (bottom-left)
slide.addShape(pres.ShapeType.ellipse, {
  x: agentsX, y: agentsY + agentGap, w: agentSize, h: agentSize,
  fill: { color: "95E1D3" },
  line: { color: "FFFFFF", width: 2 },
});
slide.addText("Market\nPulse", {
  x: agentsX, y: agentsY + agentGap + 0.2, w: agentSize, h: 0.7,
  fontSize: 12, bold: true, color: "0F1419", align: "center",
  fontFace: "Arial",
});

// Agent 4 (bottom-right)
slide.addShape(pres.ShapeType.ellipse, {
  x: agentsX + agentGap, y: agentsY + agentGap, w: agentSize, h: agentSize,
  fill: { color: "F7DC6F" },
  line: { color: "FFFFFF", width: 2 },
});
slide.addText("Concierge\nQA", {
  x: agentsX + agentGap, y: agentsY + agentGap + 0.2, w: agentSize, h: 0.7,
  fontSize: 12, bold: true, color: "0F1419", align: "center",
  fontFace: "Arial",
});

// Center hub
slide.addShape(pres.ShapeType.ellipse, {
  x: agentsX + 0.65, y: agentsY + 0.65, w: 0.8, h: 0.8,
  fill: { color: "0F1419" },
  line: { color: "00D9FF", width: 2 },
});
slide.addText("Data\nCache", {
  x: agentsX + 0.65, y: agentsY + 0.85, w: 0.8, h: 0.4,
  fontSize: 10, bold: true, color: "00D9FF", align: "center",
  fontFace: "Arial",
});

// ARROW 2
slide.addText("→", {
  x: agentsX + agentGap + 0.6, y: agentsY + 0.35, w: 0.8, h: 0.5,
  fontSize: 24, color: "00D9FF", align: "center",
});

// ═══════════════════════════════════════════════════════════════
// RESULTS (right)
// ═══════════════════════════════════════════════════════════════

const resultX = agentsX + agentGap + 1.4;
slide.addShape(pres.ShapeType.roundRect, {
  x: resultX, y: userY, w: 1.8, h: 1.2,
  fill: { color: "27AE60" },
  line: { color: "00D9FF", width: 2 },
  rectRadius: 0.2,
});
slide.addText("Deal\nRecommendations", {
  x: resultX, y: userY + 0.25, w: 1.8, h: 0.7,
  fontSize: 16, bold: true, color: "FFFFFF", align: "center",
  fontFace: "Arial",
});

// ═══════════════════════════════════════════════════════════════
// VALUE PROPOSITIONS (bottom - row 1)
// ═══════════════════════════════════════════════════════════════

const valY = 3.8;

// Value 1
slide.addText("🧠 Intent Parsing", {
  x: 0.5, y: valY, w: 2.2, h: 0.3,
  fontSize: 14, bold: true, color: "00D9FF",
  fontFace: "Arial",
});
slide.addText("Natural language → structured search", {
  x: 0.5, y: valY + 0.35, w: 2.2, h: 0.6,
  fontSize: 11, color: "CCCCCC",
  fontFace: "Arial",
});

// Value 2
slide.addText("⚖️ AI Analysis", {
  x: 3.2, y: valY, w: 2.2, h: 0.3,
  fontSize: 14, bold: true, color: "00D9FF",
  fontFace: "Arial",
});
slide.addText("Title brand detection + CARFAX warnings", {
  x: 3.2, y: valY + 0.35, w: 2.2, h: 0.6,
  fontSize: 11, color: "CCCCCC",
  fontFace: "Arial",
});

// Value 3
slide.addText("📊 Market Insights", {
  x: 5.9, y: valY, w: 2.2, h: 0.3,
  fontSize: 14, bold: true, color: "00D9FF",
  fontFace: "Arial",
});
slide.addText("Real-time pricing & market dynamics", {
  x: 5.9, y: valY + 0.35, w: 2.2, h: 0.6,
  fontSize: 11, color: "CCCCCC",
  fontFace: "Arial",
});

// Value 4
slide.addText("💬 Smart QA", {
  x: 8.6, y: valY, w: 1.4, h: 0.3,
  fontSize: 14, bold: true, color: "00D9FF",
  fontFace: "Arial",
});
slide.addText("Expert automotive advice", {
  x: 8.6, y: valY + 0.35, w: 1.4, h: 0.6,
  fontSize: 11, color: "CCCCCC",
  fontFace: "Arial",
});

// ═══════════════════════════════════════════════════════════════
// TECH STACK / TOOLS (bottom - row 2)
// ═══════════════════════════════════════════════════════════════

const techY = 5.2;
const toolSpacing = 2.0;

// Tools Header
slide.addText("🛠️  Powered By", {
  x: 0.5, y: techY - 0.3, w: 9, h: 0.25,
  fontSize: 12, bold: true, color: "00D9FF",
  fontFace: "Arial",
});

// Tool 1: Claude API
slide.addShape(pres.ShapeType.rect, {
  x: 0.5, y: techY, w: 1.8, h: 0.45,
  fill: { color: "1A1A2E" },
  line: { color: "9D4EDD", width: 1 },
  rectRadius: 0.1,
});
slide.addText("Claude API", {
  x: 0.5, y: techY + 0.08, w: 1.8, h: 0.3,
  fontSize: 11, bold: true, color: "9D4EDD", align: "center",
  fontFace: "Arial",
});

// Tool 2: FastAPI
slide.addShape(pres.ShapeType.rect, {
  x: 2.5, y: techY, w: 1.8, h: 0.45,
  fill: { color: "1A1A2E" },
  line: { color: "00D9FF", width: 1 },
  rectRadius: 0.1,
});
slide.addText("FastAPI", {
  x: 2.5, y: techY + 0.08, w: 1.8, h: 0.3,
  fontSize: 11, bold: true, color: "00D9FF", align: "center",
  fontFace: "Arial",
});

// Tool 3: SQLite
slide.addShape(pres.ShapeType.rect, {
  x: 4.5, y: techY, w: 1.8, h: 0.45,
  fill: { color: "1A1A2E" },
  line: { color: "4ECDC4", width: 1 },
  rectRadius: 0.1,
});
slide.addText("SQLite Cache", {
  x: 4.5, y: techY + 0.08, w: 1.8, h: 0.3,
  fontSize: 11, bold: true, color: "4ECDC4", align: "center",
  fontFace: "Arial",
});

// Tool 4: Data APIs
slide.addShape(pres.ShapeType.rect, {
  x: 6.5, y: techY, w: 1.8, h: 0.45,
  fill: { color: "1A1A2E" },
  line: { color: "95E1D3", width: 1 },
  rectRadius: 0.1,
});
slide.addText("MarketCheck", {
  x: 6.5, y: techY + 0.08, w: 1.8, h: 0.3,
  fontSize: 11, bold: true, color: "95E1D3", align: "center",
  fontFace: "Arial",
});

// Tool 5: Render
slide.addShape(pres.ShapeType.rect, {
  x: 8.5, y: techY, w: 1.5, h: 0.45,
  fill: { color: "1A1A2E" },
  line: { color: "27AE60", width: 1 },
  rectRadius: 0.1,
});
slide.addText("Render", {
  x: 8.5, y: techY + 0.08, w: 1.5, h: 0.3,
  fontSize: 11, bold: true, color: "27AE60", align: "center",
  fontFace: "Arial",
});

// ═══════════════════════════════════════════════════════════════
// FOOTER
// ═══════════════════════════════════════════════════════════════

slide.addText("Multi-Agent Orchestration • 24h Cache • Zero API Waste", {
  x: 0.5, y: 6.8, w: 9, h: 0.35,
  fontSize: 12, color: "00D9FF", align: "center", italic: true,
  fontFace: "Arial",
});

pres.writeFile({ fileName: "dealradar_pitch.pptx" });
console.log("✓ Slide updated with tech stack: dealradar_pitch.pptx");
