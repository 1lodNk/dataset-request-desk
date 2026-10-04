import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './styles.css';

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';
async function api(path, options={}) {
  const token = localStorage.getItem('token');
  const headers = {...(options.headers || {})};
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${API}${path}`, {...options, headers});
  if (!res.ok) { const body = await res.json().catch(()=>({detail: res.statusText})); throw new Error(body.detail || 'Request failed'); }
  if (res.status === 204) return null;
  return res.json();
}

function Login({onLogin}) {
  const [email,setEmail]=useState('client-a@example.com'), [password,setPassword]=useState('client123'), [error,setError]=useState('');
  async function submit(e){e.preventDefault();try{const data=await api('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password})});localStorage.setItem('token',data.access_token);onLogin();}catch(err){setError(err.message)}}
  return <main className="center"><form className="card" onSubmit={submit}><h1>Dataset Request Desk</h1><p className="muted">Internal robotics data operations</p><label>Email<input value={email} onChange={e=>setEmail(e.target.value)}/></label><label>Password<input type="password" value={password} onChange={e=>setPassword(e.target.value)}/></label>{error&&<p className="error">{error}</p>}<button>Sign in</button></form></main>
}

function Client({me}) {
  const [requests,setRequests]=useState([]), [task,setTask]=useState('pick cup'), [count,setCount]=useState(20), [deadline,setDeadline]=useState('2026-10-31'), [notes,setNotes]=useState(''), [error,setError]=useState('');
  async function load(){setRequests(await api('/requests'))}
  useEffect(()=>{load()},[]);
  async function create(e){e.preventDefault();try{await api('/requests',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task_name:task,episodes_requested:Number(count),deadline,notes})});setNotes('');load()}catch(err){setError(err.message)}}
  async function review(id,status){try{await api(`/requests/${id}/status`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})});load()}catch(err){setError(err.message)}}
  return <div className="page"><Header me={me}/><section className="grid"><div className="card"><h2>New request</h2><form onSubmit={create}><label>Task<input value={task} onChange={e=>setTask(e.target.value)}/></label><label>Episodes<input type="number" min="1" value={count} onChange={e=>setCount(e.target.value)}/></label><label>Deadline<input type="date" value={deadline} onChange={e=>setDeadline(e.target.value)}/></label><label>Notes<textarea value={notes} onChange={e=>setNotes(e.target.value)}/></label><button>Create request</button></form></div><div className="card"><h2>My requests</h2>{error&&<p className="error">{error}</p>}<RequestTable requests={requests} client review={review}/></div></section></div>
}

function Operator({me}) {
  const [requests,setRequests]=useState([]), [episodes,setEpisodes]=useState([]), [task,setTask]=useState(''), [quality,setQuality]=useState(''), [selected,setSelected]=useState(null), [error,setError]=useState('');
  async function load(){setRequests(await api('/requests'));const q=new URLSearchParams();if(task)q.set('task_name',task);if(quality)q.set('quality',quality);setEpisodes(await api(`/episodes?${q}`))}
  useEffect(()=>{load()},[task,quality]);
  async function status(id,status){try{await api(`/requests/${id}/status`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})});load()}catch(err){setError(err.message)}}
  async function assign(ep,req){try{await api(`/episodes/${ep}/assign/${req}`,{method:'POST'});load()}catch(err){setError(err.message)}}
  return <div className="page"><Header me={me}/><section className="card"><h2>Requests</h2>{error&&<p className="error">{error}</p>}<RequestTable requests={requests} operator status={status} onSelect={setSelected}/></section><section className="card"><h2>Episodes</h2><div className="filters"><input placeholder="task name" value={task} onChange={e=>setTask(e.target.value)}/><select value={quality} onChange={e=>setQuality(e.target.value)}><option value="">all quality</option><option>good</option><option>usable</option><option>bad</option></select></div>{selected?<p className="muted">Assigning to request #{selected.id}: {selected.task_name} ({selected.assigned_count}/{selected.episodes_requested})</p>:<p className="muted">Select a request above to assign episodes.</p>}<table><thead><tr><th>Episode</th><th>Robot</th><th>Task</th><th>Quality</th><th></th></tr></thead><tbody>{episodes.map(e=><tr key={e.id}><td>{e.episode_id}</td><td>{e.robot_id}</td><td>{e.task_name}</td><td>{e.quality}</td><td>{selected&&!e.assigned_request_id&&e.quality!=='bad'&&<button onClick={()=>assign(e.id,selected.id)}>Assign</button>}{e.assigned_request_id&&<span className="muted">Assigned #{e.assigned_request_id}</span>}</td></tr>)}</tbody></table></section></div>
}

function RequestTable({requests,client,operator,review,status,onSelect}) {return <table><thead><tr><th>ID</th><th>Task</th><th>Requested</th><th>Assigned</th><th>Status</th><th></th></tr></thead><tbody>{requests.map(r=><tr key={r.id} onClick={()=>onSelect?.(r)}><td>#{r.id}</td><td>{r.task_name}</td><td>{r.episodes_requested}</td><td>{r.assigned_count}</td><td><span className="pill">{r.status}</span></td><td>{client&&r.status==='delivered'&&<><button onClick={()=>review(r.id,'accepted')}>Accept</button> <button className="secondary" onClick={()=>review(r.id,'rejected')}>Reject</button></>}{operator&&r.status==='submitted'&&<button onClick={()=>status(r.id,'in_progress')}>Start</button>}{operator&&r.status==='in_progress'&&<button onClick={()=>status(r.id,'delivered')}>Deliver</button>}{operator&&r.status==='rejected'&&<button onClick={()=>status(r.id,'in_progress')}>Rework</button>}</td></tr>)}</tbody></table>}
function Header({me}){return <header><strong>Dataset Request Desk</strong><span>{me.name} · {me.role}</span><button className="secondary" onClick={()=>{localStorage.removeItem('token');location.reload()}}>Sign out</button></header>}
function App(){const [me,setMe]=useState(null);useEffect(()=>{if(localStorage.getItem('token'))api('/auth/me').then(setMe).catch(()=>localStorage.removeItem('token'))},[]);if(!me)return <Login onLogin={()=>api('/auth/me').then(setMe)}/>;return me.role==='client'?<Client me={me}/>:<Operator me={me}/>}

createRoot(document.getElementById('root')).render(<App/>);
