import React from 'react';
import Loadable from 'react-loadable';
import { Alert, Button, Modal, Select, Space, Spin, Table } from 'antd';

const API = process.env.NODE_ENV === 'production' ? '/api' : 'http://localhost:8001/api';
const Plot = Loadable({loader: () => import('react-plotly.js'), loading: () => <Spin />});

export default function CandidateComparison({taskId, candidates, renderPhase}) {
  const [open, setOpen] = React.useState(false);
  const [pair, setPair] = React.useState([]);
  const [data, setData] = React.useState(null);
  const [error, setError] = React.useState('');
  const [range, setRange] = React.useState(null);
  React.useEffect(() => {
    if (!open || pair.length !== 2) return undefined;
    const controller = new AbortController();
    setData(null); setError(''); setRange(null);
    fetch(`${API}/task/${taskId}/candidate-comparison?left=${pair[0]}&right=${pair[1]}`, {signal:controller.signal})
      .then(async response => { const body = await response.json(); if (!response.ok) throw new Error(body.detail || 'Comparison unavailable'); return body; })
      .then(setData).catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    return () => controller.abort();
  }, [open, taskId, pair]);
  const options = candidates.map((candidate,i) => ({value:candidate.source_index ?? i,label:`Candidate ${i+1} — Rwp ${candidate.rwp}%`}));
  const label = id => options.find(option => option.value === id)?.label || String(id);
  const names = values => values.length ? <Space direction="vertical">{values.map(value => <span key={value}>{renderPhase ? renderPhase(value) : value}</span>)}</Space> : 'None';
  return <>
    <Button style={{marginBottom:12}} disabled={candidates.length < 2} onClick={() => {setPair([options[0].value, options[1].value]); setOpen(true);}}>Compare two candidate fits</Button>
    <Modal title="Candidate differences — same measured pattern" open={open} width="95vw" footer={null} onCancel={() => setOpen(false)}>
      <Space wrap style={{marginBottom:16}}>
        {[0,1].map(side => <label key={side}>{side === 0 ? 'A' : 'B'}: <Select aria-label={`Comparison candidate ${side === 0 ? 'A' : 'B'}`} style={{width:290}} value={pair[side]}
          options={options.map(option => ({...option,disabled:option.value === pair[1-side]}))}
          onChange={id => setPair(previous => side === 0 ? [id,previous[1]] : [previous[0],id])} /></label>)}
        <Button onClick={() => setRange(null)}>Reset 2θ range</Button>
      </Space>
      {error ? <Alert showIcon type="error" message={error} /> : !data ? <Spin /> : <>
        <Table size="small" pagination={false} rowKey="group" dataSource={[
          {group:`Only A (${label(pair[0])})`,phases:data.left_only},
          {group:`Only B (${label(pair[1])})`,phases:data.right_only},
          {group:'Common formula / space-group identities',phases:data.common},
        ]} columns={[{title:'Phase-set difference',dataIndex:'group'}, {title:'Phases',dataIndex:'phases',render:names}]} />
        <Plot style={{width:'100%',height:620}} useResizeHandler
          data={[
            {x:data.x,y:data.observed,name:'Observed',type:'scatter',mode:'markers',marker:{size:3,color:'#333'}},
            {x:data.x,y:data.left_calculated,name:'A calculated',type:'scatter',mode:'lines',line:{color:'#1677ff'}},
            {x:data.x,y:data.right_calculated,name:'B calculated',type:'scatter',mode:'lines',line:{color:'#e67700',dash:'dash'}},
            {x:data.x,y:data.left_residual,name:'A obs − calc',type:'scatter',mode:'lines',xaxis:'x2',yaxis:'y2',line:{color:'#1677ff'}},
            {x:data.x,y:data.right_residual,name:'B obs − calc',type:'scatter',mode:'lines',xaxis:'x2',yaxis:'y2',line:{color:'#e67700',dash:'dash'}},
          ]}
          layout={{autosize:true,margin:{l:80,r:30,t:50,b:65},legend:{orientation:'h',y:1.1},hovermode:'x unified',
            xaxis:{domain:[0,1],anchor:'y',title:'2θ (°)',showticklabels:true,range:range || undefined,autorange:!range},
            xaxis2:{domain:[0,1],anchor:'y2',matches:'x',title:'2θ (°)',showticklabels:true,range:range || undefined,autorange:!range},
            yaxis:{domain:[0.43,1],title:'Intensity'}, yaxis2:{domain:[0,0.26],title:'Obs − calc',zeroline:true},
            uirevision:`${pair.join('-')}:${range?.join('-') || 'full'}`}}
          config={{responsive:true,displaylogo:false,toImageButtonOptions:{format:'svg',filename:`task_${taskId}_candidate_comparison`}}} />
        <p>Regions with the largest calculated-intensity differences (click to zoom):</p>
        <Space wrap>{data.regions.map(region => <Button key={region.two_theta} onClick={() => setRange([Math.max(data.x[0],region.two_theta-1),Math.min(data.x[data.x.length-1],region.two_theta+1)])}>{region.two_theta.toFixed(3)}°</Button>)}</Space>
        <p style={{marginTop:12,color:'#666'}}>These are model-disagreement regions, not proof of unique diagnostic reflections. Excluded intervals are omitted. Compare residuals, phase chemistry and physical plausibility together; lower Rwp alone does not prove correctness.</p>
      </>}
    </Modal>
  </>;
}
