"""engine/ —— 抓取后端实现层（HTTP / RSS / Git / HTML / CDP）。

职责：把「怎么把数据从某种协议搬回来」封装成可复用的纯后端能力，不含任何信源
业务语义（业务语义、白名单、字段映射等一律在 ``eyes/``）。``http.py`` 是本层唯一
出网口（``cdp.py`` 因走本地常驻浏览器/内网代理通道除外）。
"""

from __future__ import annotations
