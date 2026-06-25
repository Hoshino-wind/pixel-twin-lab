# Component Taxonomy

Use this reference when labeling `ui-blueprint.json` component semantics. The taxonomy is based on `antd list --format json` component categories, normalized to lowercase kebab-case for blueprint values.

## Categories

- `general`: Button, FloatButton, Icon, Typography.
- `layout`: Divider, Flex, Grid, Layout, Masonry, Space, Splitter.
- `navigation`: Anchor, Breadcrumb, Dropdown, Menu, Pagination, Steps, Tabs.
- `data-entry`: AutoComplete, Cascader, Checkbox, ColorPicker, DatePicker, Form, Input, InputNumber, Mentions, Radio, Rate, Select, Slider, Switch, TimePicker, Transfer, TreeSelect, Upload.
- `data-display`: Avatar, Badge, Calendar, Card, Carousel, Collapse, Descriptions, Empty, Image, List, Popover, QRCode, Segmented, Statistic, Table, Tag, Timeline, Tooltip, Tour, Tree.
- `feedback`: Alert, Drawer, Message, Modal, Notification, Popconfirm, Progress, Result, Skeleton, Spin, Watermark.
- `other`: Affix, App, BorderBeam, ConfigProvider.
- `custom`: Project-specific or reference-specific components that do not map cleanly to an Ant Design category.

## Type Values

Prefer the closest Ant Design component archetype when labeling `component.type`:

- `general`: `button`, `float-button`, `icon`, `typography`
- `layout`: `divider`, `flex`, `grid`, `layout`, `masonry`, `space`, `splitter`
- `navigation`: `anchor`, `breadcrumb`, `dropdown`, `menu`, `pagination`, `steps`, `tabs`
- `data-entry`: `auto-complete`, `cascader`, `checkbox`, `color-picker`, `date-picker`, `form`, `input`, `input-number`, `mentions`, `radio`, `rate`, `select`, `slider`, `switch`, `time-picker`, `transfer`, `tree-select`, `upload`
- `data-display`: `avatar`, `badge`, `calendar`, `card`, `carousel`, `collapse`, `descriptions`, `empty`, `image`, `list`, `popover`, `qr-code`, `segmented`, `statistic`, `table`, `tag`, `timeline`, `tooltip`, `tour`, `tree`
- `feedback`: `alert`, `drawer`, `message`, `modal`, `notification`, `popconfirm`, `progress`, `result`, `skeleton`, `spin`, `watermark`
- `other`: `affix`, `app`, `border-beam`, `config-provider`

Pixel Twin also allows project-neutral types that are useful outside Ant Design:

- `chart-container`, `map-container`, `media`
- `heading`, `text`
- `nav-item`, `menu-item`, `tab-item`, `table-row`, `list-item`
- `tooltip-anchor`, `container`, `other`

## Labeling Rules

- Set `category` to the AntD category when the component maps cleanly; set `custom` for product-specific composites such as `weather-card`, `trip-map-panel`, or `ai-assistant-panel`.
- Use `type` for the closest reusable archetype, not the exact product name. Put the product/component name in `maps_to` or `notes`.
- Composite regions should contain multiple components: for example, a header may include `layout`, `menu`, `button`, `avatar`, and `badge` components rather than one giant `container`.
- Tables, lists, trees, menus, transfer lists, and chart containers should be rendered from the blueprint `data` layer or project data props, not from repeated hard-coded sibling nodes.
- Charts/maps/3D scenes still use Pixel Twin tracks (`chart-container`, `map-container`, `approximation`) even when a project has an AntD shell around them.
