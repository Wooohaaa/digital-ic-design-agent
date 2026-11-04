# TUI 配色与 ICFORGE 字标

在 TUI 中输入 `/theme` 打开主题选择器，或使用 `/theme nord` 直接切换。主题名称、主/辅色会显示在列表中。输入 `/logo` 选择字标；也可用 `/logo 3d`、`/logo glitch`、`/logo minimal` 直接切换。主题与字标独立保存于当前工作区 `.fpga-agent/config.json`。

## 主题

| 命令 | 风格 | 文字主色 / 辅色 |
| --- | --- | --- |
| `/theme forge` | 金黄、象牙白、铜色 | `#FFD21F` · `#E7E4CE` · `#AD795A` |
| `/theme rose` | 参考图中的茶花红与柔粉白 | `#E72D48` · `#C91F3B` · `#F1DDDF` |
| `/theme citrine` | 参考图中的晴黄与莴笋绿 | `#FFE76F` · `#88AE1B` · `#FFF2A9` |
| `/theme nord` | 冰川蓝、极光绿、雪白 | `#88C0D0` · `#A3BE8C` · `#ECEFF4` |
| `/theme tokyo` | 霓虹青、紫、浅靛蓝 | `#7DCFFF` · `#BB9AF7` · `#C0CAF5` |
| `/theme mocha` | 柔和粉紫、浅薰衣草 | `#CBA6F7` · `#F5C2E7` · `#CDD6F4` |
| `/theme latte` | 莓粉、晴蓝、瓷白 | `#EA76CB` · `#04A5E5` · `#EFF1F5` |
| `/theme mint` | 翡翠薄荷、荧光青柠 | `#67E8C7` · `#B2F56C` · `#E0F5EE` |
| `/theme cobalt` | 钴蓝、信号青、银白 | `#7AA2F7` · `#56C5D0` · `#E2EAF7` |

所有主题都使用纯黑背景；主题只切换文字、字标、输入光标和线框的颜色，不会铺设彩色面板。选中项仍保持黑底，只用主题强调色显示文字。正文使用较亮的前景色，低饱和颜色只用于次级信息和边框。

## 字标

| 命令 | 样式 |
| --- | --- |
| `/logo block` | 当前主标：粗体符号拼字，宽终端显示完整轮廓 |
| `/logo 3d` | 3D ASCII：符号字形带斜向挤出阴影 |
| `/logo glitch` | Pixel Glitch：切片、偏移和噪点符号形成故障效果 |
| `/logo minimal` | Minimal：字符框线与简化副标 |

这些字标只使用终端文本字符。Block、3D 和 Glitch 会根据可用宽度缩放；Minimal 适合窄窗口。没有新增图像或字体运行依赖。

## 调色板参考

- 用户提供的 Rose Milk、Citrine Grove 两组参考颜色。
- [Nord 官方颜色与调色板](https://www.nordtheme.com/docs/colors-and-palettes/)：Polar Night、Snow Storm、Frost 和 Aurora。
- [Tokyo Night 官方主题与色板](https://github.com/tokyo-night/tokyo-night-vscode-theme)：Night 与 Storm 的靛色背景、青蓝和紫色强调。
- [Catppuccin 官方调色板](https://catppuccin.com/palette/) 与 [样式指南](https://github.com/catppuccin/catppuccin/blob/main/docs/style-guide.md)：Mocha 深色与 Latte 浅色变体，并按其指南优先保持正文可读性。
