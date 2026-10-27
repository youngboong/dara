import React from 'react';
import { Alert, Button, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Tooltip, Typography, Upload } from 'antd';
import { DeleteOutlined, UploadOutlined, WarningOutlined } from '@ant-design/icons';
import { libraryOptionText, mpKeyTag, responseDetailText, validationText } from './libraryHelpers.mjs';

const API = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';

/** Load the reference CIF library. ``available`` stays false when the endpoint is absent. */
export function useReferenceLibrary() {
  const [items, setItems] = React.useState([]);
  const [available, setAvailable] = React.useState(false);
  const reload = React.useCallback(() => fetch(`${API}/reference-cifs`)
    .then(response => response.ok ? response.json() : Promise.reject(new Error('unavailable')))
    .then(body => { setItems(Array.isArray(body.items) ? body.items : []); setAvailable(true); })
    .catch(() => { setItems([]); setAvailable(false); }), []);
  React.useEffect(() => { reload(); }, [reload]);
  return { items, available, reload };
}

function WarningMark({ validation }) {
  if (!validation?.length) return null;
  return <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{validationText(validation)}</div>}>
    <WarningOutlined style={{ color: '#fa8c16', marginLeft: 6 }} aria-label="Validation warnings" />
  </Tooltip>;
}

function LibraryManager({ library, open, onClose }) {
  const [note, setNote] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState('');
  const { reload } = library;

  // Entries can change on the server (another tab, a manual edit); refresh whenever the dialog opens.
  React.useEffect(() => { if (open) reload(); }, [open, reload]);

  const upload = async file => {
    setBusy(true);
    setError('');
    try {
      const body = new FormData();
      body.append('file', file);
      body.append('note', note);
      const response = await fetch(`${API}/reference-cifs`, { method: 'POST', body });
      if (!response.ok) throw new Error(await responseDetailText(response));
    } catch (e) {
      setError(String(e.message || e));
      setBusy(false);
      return;
    }
    // Uploaded on the server; a follow-up UI problem must not look like a failed upload.
    try {
      setNote('');
      await library.reload();
    } catch (e) {
      console.error('Post-upload refresh failed (the upload itself succeeded):', e);
    } finally {
      setBusy(false);
    }
  };

  const remove = async id => {
    setError('');
    try {
      const response = await fetch(`${API}/reference-cifs/${encodeURIComponent(id)}`, { method: 'DELETE' });
      if (!response.ok) throw new Error(await responseDetailText(response));
    } catch (e) {
      setError(String(e.message || e));
      return;
    }
    // Deleted on the server; a follow-up UI problem must not look like a failed delete.
    try {
      await library.reload();
    } catch (e) {
      console.error('Post-delete refresh failed (the delete itself succeeded):', e);
    }
  };

  return <Modal title="Reference CIF library" open={open} onCancel={onClose} footer={null} width={900}>
    <Typography.Paragraph type="secondary">
      CIF files kept here can be selected for any analysis without uploading them again. Files are checked when they are added; warnings are advisory.
    </Typography.Paragraph>
    {error ? <Alert type="error" showIcon closable message={error} onClose={() => setError('')} style={{ marginBottom: 12 }} /> : null}
    <Space wrap style={{ marginBottom: 12 }}>
      <Input placeholder="Note (optional)" value={note} onChange={event => setNote(event.target.value)} style={{ width: 280 }} maxLength={200} aria-label="Library note" />
      <Upload accept=".cif" showUploadList={false} beforeUpload={file => { upload(file); return false; }} disabled={busy}>
        <Button icon={<UploadOutlined />} loading={busy}>Add CIF to library</Button>
      </Upload>
    </Space>
    <Table size="small" rowKey="id" dataSource={library.items} pagination={{ pageSize: 8, hideOnSinglePage: true }} scroll={{ x: 760 }}
      locale={{ emptyText: 'The library is empty.' }}
      columns={[
        { title: 'Formula', dataIndex: 'formula', render: value => value || '—' },
        { title: 'SG', dataIndex: 'spacegroup_number', render: value => value ?? '—' },
        { title: 'File', dataIndex: 'filename' },
        { title: 'Added', dataIndex: 'added_at', render: value => value ? String(value).slice(0, 16).replace('T', ' ') : '—' },
        { title: 'Note', dataIndex: 'note', render: value => value || '—' },
        { title: 'Warnings', dataIndex: 'validation', render: validation => validation?.length
          ? <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{validationText(validation)}</div>}><Tag color="orange">{validation.length}</Tag></Tooltip>
          : '—' },
        { title: '', key: 'delete', render: (_, row) => <Popconfirm title="Remove this CIF from the library?" okText="Delete" okButtonProps={{ danger: true }} cancelText="Cancel" onConfirm={() => remove(row.id)}>
          <Button size="small" danger icon={<DeleteOutlined />} aria-label={`Delete ${row.filename}`}>Delete</Button>
        </Popconfirm> },
      ]} />
  </Modal>;
}

/** Form item that picks library CIFs; renders nothing when the library API is absent. */
export function ReferenceLibraryField({ library }) {
  const [managing, setManaging] = React.useState(false);
  if (!library.available) return null;
  return <>
    <Form.Item label="Reference library CIFs" extra="Reference structures stored on this server; they are used together with the databases above.">
      <Space align="start" wrap style={{ width: '100%' }}>
        <Form.Item name="library_cif_ids" noStyle>
          <Select mode="multiple" allowClear style={{ minWidth: 340 }} placeholder="Select library CIFs"
            onDropdownVisibleChange={visible => { if (visible) library.reload(); }}
            filterOption={(input, option) => String(option?.text || '').toLowerCase().includes(input.toLowerCase())}
            options={library.items.map(item => ({
              value: item.id,
              text: libraryOptionText(item),
              label: <span>{libraryOptionText(item)}<WarningMark validation={item.validation} /></span>,
            }))} />
        </Form.Item>
        <Button onClick={() => setManaging(true)}>Manage library</Button>
      </Space>
    </Form.Item>
    <LibraryManager library={library} open={managing} onClose={() => setManaging(false)} />
  </>;
}

/** Materials Project key status tag with a re-check link; renders nothing on failure. */
export function MpKeyStatus() {
  const [status, setStatus] = React.useState(null);
  const [checking, setChecking] = React.useState(false);
  const load = React.useCallback(refresh => {
    setChecking(true);
    return fetch(`${API}/mp-key/status${refresh ? '?refresh=true' : ''}`)
      .then(response => response.ok ? response.json() : Promise.reject(new Error('unavailable')))
      .then(setStatus)
      .catch(() => setStatus(null))
      .finally(() => setChecking(false));
  }, []);
  React.useEffect(() => { load(false); }, [load]);
  const tag = mpKeyTag(status);
  if (!tag) return null;
  return <span style={{ marginLeft: 12 }}>
    <Tooltip title={tag.tooltip}><Tag color={tag.color} style={{ marginRight: 4 }}>{tag.text}</Tag></Tooltip>
    <Button type="link" size="small" loading={checking} onClick={() => load(true)}>Re-check</Button>
  </span>;
}
