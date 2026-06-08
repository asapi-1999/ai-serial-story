// 共有ユーティリティ：青空文庫式ルビ（漢字《よみ》／｜親文字《よみ》）の描画。
// すべて textContent / createTextNode 経由なのでHTMLとして解釈されない（XSS安全）。
(function (global) {
  var KANJI = '一-鿿々-〇ヶ'; // 漢字 + 々〆〇ヶ

  function makeRe() {
    return new RegExp(
      '｜([^｜《》]+)《([^《》]+)》' +          // ｜親文字《よみ》
      '|([' + KANJI + ']+)《([^《》]+)》',      // 漢字列《よみ》
      'g'
    );
  }

  // text を解析し、ルビは <ruby> として container に追記する。
  function renderRuby(container, text) {
    var re = makeRe(), last = 0, m;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) {
        container.appendChild(document.createTextNode(text.slice(last, m.index)));
      }
      var base = m[1] !== undefined ? m[1] : m[3];
      var reading = m[2] !== undefined ? m[2] : m[4];
      var ruby = document.createElement('ruby');
      ruby.appendChild(document.createTextNode(base));
      var rt = document.createElement('rt');
      rt.textContent = reading;
      ruby.appendChild(rt);
      container.appendChild(ruby);
      last = re.lastIndex;
    }
    if (last < text.length) {
      container.appendChild(document.createTextNode(text.slice(last)));
    }
  }

  // ルビ記号を取り除いて素のテキストにする（<ruby> を使えない document.title 等で使用）。
  function stripRuby(text) {
    return String(text)
      .replace(/｜([^｜《》]+)《[^《》]+》/g, '$1')
      .replace(new RegExp('([' + KANJI + ']+)《[^《》]+》', 'g'), '$1');
  }

  global.renderRuby = renderRuby;
  global.stripRuby = stripRuby;
})(window);
