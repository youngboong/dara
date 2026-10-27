import React from 'react';
import styled from 'styled-components';
import Layout from '../components/Layout';
import { Button, Input, Popconfirm, Space, Table, Tag, Tooltip, message } from 'antd';
import { DeleteOutlined, DownloadOutlined, LineChartOutlined, TableOutlined } from '@ant-design/icons';
import { parseComparisonTaskIds } from '../components/patternComparison.mjs';
import { runAfterSuccess } from '../components/submitHelpers.mjs';
import { adoptedLabel, formatFixed, phasesLabel, phasesTooltip, tasksQuery } from '../components/resultsList.mjs';

const URL = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';

const Container = styled.div`
`

const { Search } = Input;

export const STATE_COLOR = {
  'COMPLETED': 'green',
  'FIZZLED': 'red',
  'PENDING': 'blue',
  'RUNNING': 'yellow',
  'READY': 'blue',
  'CANCELLING': 'orange',
  'CANCELLED': 'default'
}

function ResultsPage({ location }) {
  const queryParameters = new URLSearchParams(location.search)
  const page = Number((queryParameters.get('page')) || "1")
  const limit = Number(queryParameters.get('limit') || "10")
  const [ user, setUser ] = React.useState(queryParameters.get('user') || null)
  const [ name, setName ] = React.useState(queryParameters.get('name') || null)

  const [data, setData] = React.useState({})
  const [selectedTaskIds, setSelectedTaskIds] = React.useState(() => parseComparisonTaskIds(queryParameters.get('selected')));

  const onSearch = (value) => {
    setUser(value)
    window.location.href = `/results?` + tasksQuery({ page: 1, limit, user: value, name, selected: selectedTaskIds })
  }

  const onNameSearch = (value) => {
    const trimmed = (value || '').trim()
    setName(trimmed || null)
    window.location.href = `/results?` + tasksQuery({ page: 1, limit, user, name: trimmed, selected: selectedTaskIds })
  }

  React.useEffect(() => {
    fetch(`${URL}/tasks?page=${page}&limit=${limit}` + (user ? `&user=${encodeURIComponent(user)}` : "") + (name ? `&name=${encodeURIComponent(name)}` : ""))
      .then(response => response.json())
      .then(data => setData(data));
  }, [page, limit, user, name]);

  const deleteTask = async (taskId) => {
    const response = await fetch(`${URL}/task/${taskId}`, { method: 'DELETE' });
    if (!response.ok) {
      const error = await response.json();
      message.error(error.detail || 'Could not delete the result.');
      return;
    }
    // The result is already deleted on the server; UI follow-up problems must not look like a failed delete.
    runAfterSuccess([
      () => setData(previous => ({
        ...previous,
        tasks: (previous.tasks || []).filter(task => task.fw_id !== taskId),
        total_tasks: Math.max((previous.total_tasks || 1) - 1, 0),
      })),
      () => setSelectedTaskIds(previous => previous.filter(id => id !== taskId)),
      () => message.success(`Task ${taskId} deleted.`),
    ]);
  };

  const columns = [
    {
      title: 'ID #',
      dataIndex: 'fw_id',
      key: 'fw_id',
    },
    {
      title: 'Name',
      dataIndex: 'name',
      key: 'name',
      render: (text, entry) => (
        <>
          <a href={`/task?id=${entry.fw_id}`}>{text}</a>
          {entry.series?.id ? (
            <div>
              <Tag color="geekblue" style={{ marginTop: 4 }}>
                <a href={`/series/?id=${encodeURIComponent(entry.series.id)}`}>{`Series: ${entry.series.name} #${(entry.series.order ?? 0) + 1}`}</a>
              </Tag>
            </div>
          ) : null}
        </>
      )
    },
    {
      title: 'Status',
      dataIndex: 'state',
      key: 'state',
      render: (text, entry) => {
        const tag = <Tag color={STATE_COLOR[text] ? STATE_COLOR[text] : 'grey'} key={text}>{text}</Tag>
        return text === 'FIZZLED' && entry.error_summary?.title
          ? <Tooltip title={entry.error_summary.title}>{tag}</Tooltip>
          : tag
      }
    },
    {
      title: 'Best Rwp (%)',
      key: 'best_rwp',
      render: (_, entry) => formatFixed(entry.summary?.best_rwp),
    },
    {
      title: <Tooltip title="Goodness of fit = Rwp / Rexp of the final refinement">GOF</Tooltip>,
      key: 'gof',
      render: (_, entry) => formatFixed(entry.summary?.gof),
    },
    {
      title: 'Final phases',
      key: 'final_phases',
      width: 240,
      render: (_, entry) => {
        const label = phasesLabel(entry.summary)
        if (!label) return '—'
        return (
          <Tooltip title={<div style={{ whiteSpace: 'pre-line' }}>{phasesTooltip(entry.summary)}</div>}>
            <span style={{ display: 'inline-block', maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', verticalAlign: 'bottom' }}>{label}</span>
          </Tooltip>
        )
      }
    },
    {
      title: <Tooltip title="Result marked as adopted in the task review">Adopted</Tooltip>,
      key: 'adopted',
      render: (_, entry) => {
        const label = adoptedLabel(entry.adopted)
        return label ? <Tag color={label.color}>{label.text}</Tag> : '—'
      }
    },
    {
      title: 'Created',
      dataIndex: 'created_on',
      key: 'created_on',
    },
    {
      title: 'Submitted by',
      dataIndex: 'user',
      key: 'user',
    },
    {
      title: 'Actions',
      key: 'actions',
      render: (_, entry) => (
        <Space>
          <Button
            icon={<DownloadOutlined />}
            href={`${URL}/task/${entry.fw_id}/download`}
            disabled={entry.state !== 'COMPLETED'}
          >
            Download
          </Button>
          <Popconfirm
            title={`Delete task ${entry.fw_id}?`}
            description="This permanently removes the stored result."
            okText="Delete"
            okButtonProps={{ danger: true }}
            cancelText="Cancel"
            onConfirm={() => deleteTask(entry.fw_id)}
            disabled={['READY','RUNNING','CANCELLING'].includes(entry.state)}
          >
            <Button
              danger
              icon={<DeleteOutlined />}
              disabled={['READY','RUNNING','CANCELLING'].includes(entry.state)}
            >
              Delete
            </Button>
          </Popconfirm>
        </Space>
      ),
    }
  ]

  return (
    <Layout hasSider={false} title="">
      <Container>
        <div style={{ display: "flex", flexDirection: "row-reverse", gap: 12, flexWrap: "wrap" }}>
          <Search
            placeholder="Filter username"
            allowClear
            enterButton
            size="middle"
            onSearch={onSearch}
            defaultValue={user}
            style={{ width: 400, marginBottom: "24px" }}
          />
          <Search
            placeholder="Filter file name"
            allowClear
            enterButton
            size="middle"
            onSearch={onNameSearch}
            defaultValue={name}
            style={{ width: 400, marginBottom: "24px" }}
          />
        </div>
        <Space wrap style={{ marginBottom: 16 }}>
          <Button
            icon={<LineChartOutlined />}
            disabled={selectedTaskIds.length < 2}
            href={selectedTaskIds.length >= 2 ? `/compare?tasks=${selectedTaskIds.join(',')}` : undefined}
          >Compare selected ({selectedTaskIds.length})</Button>
          <Button
            icon={<TableOutlined />}
            disabled={!selectedTaskIds.length}
            href={selectedTaskIds.length ? `${URL}/tasks/table.csv?ids=${selectedTaskIds.join(',')}` : undefined}
          >Export table ({selectedTaskIds.length})</Button>
          <span>Select completed tasks to overlay patterns (2–20) or export their final results as CSV.</span>
          {selectedTaskIds.length > 0 && <Button type="link" onClick={() => setSelectedTaskIds([])}>Clear selection</Button>}
        </Space>
        {
          data.tasks ? (
            <Table 
              rowKey="fw_id"
              scroll={{ x: 'max-content' }}
              dataSource={data.tasks}
              columns={columns} 
              rowSelection={{
                selectedRowKeys: selectedTaskIds,
                preserveSelectedRowKeys: true,
                onChange: keys => {
                  if (keys.length > 20) message.warning('Compare up to 20 patterns at a time.');
                  setSelectedTaskIds(keys.slice(0, 20));
                },
                getCheckboxProps: record => ({ disabled: record.state !== 'COMPLETED' }),
              }}
              pagination={{
                total: data.total_tasks,
                pageSize: limit,
                current: page,
                onChange: (page, pageSize) => {
                  window.location.href = `/results?` + tasksQuery({ page, limit: pageSize, user, name, selected: selectedTaskIds })
                }
            }}/>
          ) : (
            <p>Loading...</p>
          )
        }
      </Container>

    </Layout>
  )
}

export default ResultsPage;
