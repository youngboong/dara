import React from 'react';
import styled from 'styled-components';
import Layout from '../components/Layout';
import { Button, Input, Popconfirm, Space, Table, Tag, message } from 'antd';
import { DeleteOutlined, DownloadOutlined, LineChartOutlined } from '@ant-design/icons';
import { parseComparisonTaskIds } from '../components/patternComparison.mjs';

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

  const [data, setData] = React.useState({})
  const [selectedTaskIds, setSelectedTaskIds] = React.useState(() => parseComparisonTaskIds(queryParameters.get('selected')));
  const [messageApi, contextHolder] = message.useMessage();

  const onSearch = (value) => {
    setUser(value)
    window.location.href = `/results?page=1&limit=${limit}` + (value ? `&user=${encodeURIComponent(value)}` : "") + (selectedTaskIds.length ? `&selected=${selectedTaskIds.join(',')}` : "")
  }

  React.useEffect(() => {
    fetch(`${URL}/tasks?page=${page}&limit=${limit}` + (user ? `&user=${user}` : ""))
      .then(response => response.json())
      .then(data => setData(data));
  }, [page, limit, user]);

  const deleteTask = async (taskId) => {
    const response = await fetch(`${URL}/task/${taskId}`, { method: 'DELETE' });
    if (!response.ok) {
      const error = await response.json();
      messageApi.error(error.detail || 'Could not delete the result.');
      return;
    }
    setData(previous => ({
      ...previous,
      tasks: (previous.tasks || []).filter(task => task.fw_id !== taskId),
      total_tasks: Math.max((previous.total_tasks || 1) - 1, 0),
    }));
    setSelectedTaskIds(previous => previous.filter(id => id !== taskId));
    messageApi.success(`Task ${taskId} deleted.`);
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
      render: (text, entry) => <a href={`/task?id=${entry.fw_id}`}>{text}</a>
    },
    {
      title: 'Status',
      dataIndex: 'state',
      key: 'state',
      render: (text) => {
        return (
          <Tag color={STATE_COLOR[text] ? STATE_COLOR[text] : 'grey'} key={text}>{text}</Tag>
        )
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
        {contextHolder}
        <div style={{ display: "flex", flexDirection: "row-reverse" }}>
          <Search
            placeholder="Filter username"
            allowClear
            enterButton
            size="middle"
            onSearch={onSearch}
            defaultValue={user}
            style={{ width: 400, marginBottom: "24px" }}
          />
        </div>
        <Space wrap style={{ marginBottom: 16 }}>
          <Button
            icon={<LineChartOutlined />}
            disabled={selectedTaskIds.length < 2}
            href={selectedTaskIds.length >= 2 ? `/compare?tasks=${selectedTaskIds.join(',')}` : undefined}
          >Compare selected ({selectedTaskIds.length})</Button>
          <span>Select 2–20 completed tasks to overlay measured patterns.</span>
          {selectedTaskIds.length > 0 && <Button type="link" onClick={() => setSelectedTaskIds([])}>Clear selection</Button>}
        </Space>
        {
          data.tasks ? (
            <Table 
              rowKey="fw_id"
              dataSource={data.tasks}
              columns={columns} 
              rowSelection={{
                selectedRowKeys: selectedTaskIds,
                preserveSelectedRowKeys: true,
                onChange: keys => {
                  if (keys.length > 20) messageApi.warning('Compare up to 20 patterns at a time.');
                  setSelectedTaskIds(keys.slice(0, 20));
                },
                getCheckboxProps: record => ({ disabled: record.state !== 'COMPLETED' }),
              }}
              pagination={{
                total: data.total_tasks,
                pageSize: limit,
                current: page,
                onChange: (page, pageSize) => {
                  window.location.href = `/results?page=${page}&limit=${pageSize}` + (user ? `&user=${encodeURIComponent(user)}` : "") + (selectedTaskIds.length ? `&selected=${selectedTaskIds.join(',')}` : "")
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
