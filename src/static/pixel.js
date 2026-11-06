// Pixel glyphs for seasons and observances: 8x8 bitmaps drawn crisply at any size.
// '#' is the glyph colour, '+' a highlight, '.' transparent.
(function () {
  "use strict";

  const GLYPHS = {
    // Seasons
    snowflake: ["...#....", ".#.#.#..", "..###...", "#######.", "..###...", ".#.#.#..", "...#....", "........"],
    sprout:    ["........", ".##..##.", "#++##++#", ".#.##.#.", "...##...", "...##...", "..####..", ".######."],
    leaf:      [".....##.", "...####.", "..#####.", ".####+#.", ".###+##.", "..#+##..", ".#+.....", "#......."],
    wheat:     ["..#.#...", ".#+#+#..", "..###...", ".#+#+#..", "..###...", "...#....", "...#....", "...#...."],
    feather:   ["......#.", ".....##.", "....#+#.", "...#+#..", "..#+#...", ".###....", ".#......", "#......."],
    star:      ["....#...", "...#+#..", "########", ".##++##.", "..####..", "..#..#..", ".#....#.", "........"],
    // Observance kinds
    feast:             ["...+....", "..+#+...", "...#....", "..###...", "..#+#...", "..###...", "..###...", ".#####.."],
    memorial:          ["..#.#...", ".#####..", "..#+#...", ".#####..", "..#.#...", "...#....", "..##....", "...#...."],
    commemoration:     ["...++...", "...++...", "...##...", "..####..", ".##++##.", "..####..", "...##...", "..####.."],
    day_of_regression: ["#......#", ".#.##.#.", "..####..", "#.#++#.#", ".######.", "#.####.#", "..####..", ".#....#."],
  };

  function draw(canvas) {
    const rows = GLYPHS[canvas.dataset.glyph];
    if (!rows) return;
    const size = parseInt(canvas.dataset.size || "16", 10);
    const scale = Math.max(1, Math.round(size / 8));
    const color = canvas.dataset.color || getComputedStyle(canvas).color;
    canvas.width = canvas.height = 8 * scale;
    canvas.style.width = canvas.style.height = 8 * scale + "px";
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    rows.forEach(function (row, y) {
      for (let x = 0; x < row.length; x++) {
        const c = row[x];
        if (c === ".") continue;
        ctx.fillStyle = c === "+" ? "#fff8e6" : color;
        ctx.fillRect(x * scale, y * scale, scale, scale);
      }
    });
  }

  function drawAll(root) {
    (root || document).querySelectorAll("canvas[data-glyph]").forEach(draw);
  }

  window.PixelGlyphs = { draw: draw, drawAll: drawAll };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { drawAll(); });
  } else {
    drawAll();
  }
})();
