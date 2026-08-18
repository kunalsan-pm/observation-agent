/* ============================================================
   Minimal markdown renderer.

   Deliberately dependency-free: the app has no build step and a strict
   no-external-hosts posture, so pulling in a library is not an option.
   Covers what the agent actually emits — headings, tables, lists, code,
   blockquotes, emphasis, links, rules.

   All text is escaped before any markup is inserted, so model output can
   never inject HTML.
   ============================================================ */
(function () {
  "use strict";

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Inline spans. Code first, so its contents are never re-processed.
  function inline(text) {
    var codes = [];
    var out = escapeHtml(text).replace(/`([^`]+)`/g, function (_, code) {
      codes.push(code);
      return "\u0000" + (codes.length - 1) + "\u0000";
    });

    out = out
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (_, label, href) {
        if (!/^(https?:|mailto:|#|\/)/i.test(href)) return label;
        return '<a href="' + href + '" rel="noopener noreferrer" target="_blank">' + label + "</a>";
      })
      .replace(/\*\*\*([^*]+)\*\*\*/g, "<strong><em>$1</em></strong>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
      .replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
      .replace(/~~([^~]+)~~/g, "<del>$1</del>");

    return out.replace(/\u0000(\d+)\u0000/g, function (_, i) {
      return "<code>" + escapeHtml(codes[Number(i)]) + "</code>";
    });
  }

  function isTableDivider(line) {
    return /^\s*\|?[\s:\-|]+\|[\s:\-|]*$/.test(line) && line.indexOf("-") !== -1;
  }

  function splitRow(line) {
    var t = line.trim().replace(/^\|/, "").replace(/\|$/, "");
    return t.split("|").map(function (c) { return c.trim(); });
  }

  function alignments(divider) {
    return splitRow(divider).map(function (c) {
      if (/^:.*:$/.test(c)) return "center";
      if (/:$/.test(c)) return "right";
      if (/^:/.test(c)) return "left";
      return "";
    });
  }

  function render(src) {
    var lines = String(src || "").replace(/\r\n?/g, "\n").split("\n");
    var html = [];
    var i = 0;

    while (i < lines.length) {
      var line = lines[i];

      // fenced code
      var fence = line.match(/^\s*(`{3,}|~{3,})\s*([\w+-]*)\s*$/);
      if (fence) {
        var marker = fence[1][0];
        var body = [];
        i++;
        while (i < lines.length && !new RegExp("^\\s*" + marker + "{3,}\\s*$").test(lines[i])) {
          body.push(lines[i]); i++;
        }
        i++;
        html.push("<pre><code>" + escapeHtml(body.join("\n")) + "</code></pre>");
        continue;
      }

      if (!line.trim()) { i++; continue; }

      // horizontal rule
      if (/^\s*(\*\s*){3,}$/.test(line) || /^\s*(-\s*){3,}$/.test(line) || /^\s*(_\s*){3,}$/.test(line)) {
        html.push("<hr />"); i++; continue;
      }

      // heading
      var h = line.match(/^\s*(#{1,6})\s+(.*)$/);
      if (h) {
        var lvl = h[1].length;
        html.push("<h" + lvl + ">" + inline(h[2].replace(/\s+#+\s*$/, "")) + "</h" + lvl + ">");
        i++; continue;
      }

      // table: header row followed by a divider
      if (line.indexOf("|") !== -1 && i + 1 < lines.length && isTableDivider(lines[i + 1])) {
        var heads = splitRow(line);
        var align = alignments(lines[i + 1]);
        i += 2;
        var rows = [];
        while (i < lines.length && lines[i].indexOf("|") !== -1 && lines[i].trim()) {
          rows.push(splitRow(lines[i])); i++;
        }
        var t = ['<div class="md-table-wrap"><table><thead><tr>'];
        heads.forEach(function (cell, idx) {
          var st = align[idx] ? ' style="text-align:' + align[idx] + '"' : "";
          t.push("<th" + st + ">" + inline(cell) + "</th>");
        });
        t.push("</tr></thead><tbody>");
        rows.forEach(function (row) {
          t.push("<tr>");
          for (var c = 0; c < heads.length; c++) {
            var st2 = align[c] ? ' style="text-align:' + align[c] + '"' : "";
            t.push("<td" + st2 + ">" + inline(row[c] === undefined ? "" : row[c]) + "</td>");
          }
          t.push("</tr>");
        });
        t.push("</tbody></table></div>");
        html.push(t.join(""));
        continue;
      }

      // blockquote
      if (/^\s*>\s?/.test(line)) {
        var quote = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          quote.push(lines[i].replace(/^\s*>\s?/, "")); i++;
        }
        html.push("<blockquote>" + render(quote.join("\n")) + "</blockquote>");
        continue;
      }

      // lists, with one level of nesting
      var bullet = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
      if (bullet) {
        var ordered = /\d/.test(bullet[2]);
        var tag = ordered ? "ol" : "ul";
        var items = [];
        var baseIndent = bullet[1].length;

        while (i < lines.length) {
          var m = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
          if (!m) {
            // a continuation line keeps the current item open
            if (lines[i].trim() && items.length && /^\s{2,}/.test(lines[i])) {
              items[items.length - 1].text += " " + lines[i].trim(); i++; continue;
            }
            break;
          }
          if (m[1].length > baseIndent) {
            items[items.length - 1].children.push(lines[i].slice(baseIndent + 1));
            i++; continue;
          }
          if (m[1].length < baseIndent) break;
          items.push({ text: m[3], children: [] });
          i++;
        }

        html.push(
          "<" + tag + ">" +
          items.map(function (item) {
            var nested = item.children.length ? render(item.children.join("\n")) : "";
            return "<li>" + inline(item.text) + nested + "</li>";
          }).join("") +
          "</" + tag + ">"
        );
        continue;
      }

      // paragraph
      var para = [];
      while (
        i < lines.length && lines[i].trim() &&
        !/^\s*(#{1,6})\s+/.test(lines[i]) &&
        !/^\s*>\s?/.test(lines[i]) &&
        !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i]) &&
        !/^\s*(`{3,}|~{3,})/.test(lines[i]) &&
        !(lines[i].indexOf("|") !== -1 && i + 1 < lines.length && isTableDivider(lines[i + 1]))
      ) {
        para.push(lines[i]); i++;
      }
      if (para.length) html.push("<p>" + inline(para.join("\n")) + "</p>");
      else i++;
    }

    return html.join("\n");
  }

  window.md = { render: render, escapeHtml: escapeHtml };
})();
