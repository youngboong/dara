import React from 'react';
import { Alert, Button, Dropdown, Space, Tag, Tooltip } from 'antd';
import { DownOutlined, ExportOutlined, FileImageOutlined } from '@ant-design/icons';
import {
  EXPORT_FORMATS, displacementTag, evidenceLines, exportHref, figureHref, overallAlertType,
  overallColor, reasonsText, screeningTags, verdictColor,
} from './confidence.mjs';

const API = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';

const lines = values => <div style={{ whiteSpace: 'pre-line' }}>{values.join('\n')}</div>;

/** Overall banner at the top of a completed result; nothing for tasks without confidence data. */
export function ConfidenceBanner({ confidence }) {
  const overall = confidence?.overall;
  if (!overall?.level) return null;
  const reasons = reasonsText(overall);
  return <Alert
    style={{ marginBottom: 16 }}
    showIcon
    type={overallAlertType(overall.level)}
    message={overall.label || overall.level}
    description={<>
      {reasons.length ? <ul style={{ margin: 0, paddingLeft: 20 }}>{reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul> : null}
      <a href="/tutorial#result-confidence">How is this assessed?</a>
    </>}
  />;
}

/** Per-phase verdict tag with the evidence behind it in the tooltip. */
export function VerdictTag({ verdict }) {
  if (!verdict?.level) return <span>—</span>;
  const content = [...reasonsText(verdict), ...evidenceLines(verdict.evidence).map(line => `• ${line}`)];
  const tag = <Tag color={verdictColor(verdict.level)}>{verdict.label || verdict.level}</Tag>;
  return content.length ? <Tooltip title={lines(content)}>{tag}</Tooltip> : tag;
}

/** Overall candidate tag for the candidates table. */
export function OverallTag({ confidence }) {
  const overall = confidence?.overall;
  if (!overall?.level) return <span>—</span>;
  const reasons = reasonsText(overall);
  const tag = <Tag color={overallColor(overall.level)}>{overall.label || overall.level}</Tag>;
  return reasons.length ? <Tooltip title={lines(reasons)}>{tag}</Tooltip> : tag;
}

/** Specimen-displacement estimate as a coloured tag. */
export function DisplacementTag({ confidence }) {
  const tag = displacementTag(confidence?.sample_displacement);
  if (!tag) return null;
  const element = <Tag color={tag.color}>{tag.text}</Tag>;
  return tag.tooltip ? <Tooltip title={lines([tag.tooltip])}>{element}</Tooltip> : element;
}

/** Tags describing candidate screening (CIFs excluded, computed-only phases, widened window). */
export function ScreeningTags({ candidateSummary }) {
  const tags = screeningTags(candidateSummary);
  if (!tags.length) return null;
  return <Space size={[4, 4]} wrap style={{ marginTop: 4 }}>
    {tags.map(tag => <Tooltip key={tag.key} title={tag.tooltip ? lines([tag.tooltip]) : undefined}>
      <Tag color={tag.color}>{tag.text}</Tag>
    </Tooltip>)}
  </Space>;
}

/** Figure (PNG/SVG) and structure-export downloads for one result selection. */
export function FigureExportActions({ taskId, selection, size }) {
  const items = EXPORT_FORMATS.map(format => ({
    key: format.key,
    label: <a href={exportHref(API, taskId, format.key, selection)}>{format.label}</a>,
  }));
  return <>
    <Button size={size} icon={<FileImageOutlined />} href={figureHref(API, taskId, 'png', selection)}>Figure (PNG)</Button>
    <Button size={size} icon={<FileImageOutlined />} href={figureHref(API, taskId, 'svg', selection)}>Figure (SVG)</Button>
    <Dropdown menu={{ items }} trigger={['click']}>
      <Button size={size} icon={<ExportOutlined />}>Export for… <DownOutlined /></Button>
    </Dropdown>
  </>;
}
