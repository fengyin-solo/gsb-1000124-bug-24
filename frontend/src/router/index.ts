import { createRouter, createWebHistory } from 'vue-router'

import Dashboard from '@/views/Dashboard.vue'
const Sample = () => import('@/views/sample/index.vue')
const Task = () => import('@/views/task/index.vue')
const Instrument = () => import('@/views/instrument/index.vue')
const Calibration = () => import('@/views/calibration/index.vue')
const Reagent = () => import('@/views/reagent/index.vue')
const Result = () => import('@/views/result/index.vue')
const ResultEntry = () => import('@/views/result/entry.vue')
const Report = () => import('@/views/report/index.vue')
const Qc = () => import('@/views/qc/index.vue')
const Deviation = () => import('@/views/deviation/index.vue')
const SampleStorage = () => import('@/views/sample_storage/index.vue')
const Contract = () => import('@/views/contract/index.vue')
const Staff = () => import('@/views/staff/index.vue')
const Method = () => import('@/views/method/index.vue')
const Environment = () => import('@/views/environment/index.vue')
const Complain = () => import('@/views/complain/index.vue')
const Audit = () => import('@/views/audit/index.vue')
const EquipmentRepair = () => import('@/views/equipment_repair/index.vue')
const Document = () => import('@/views/document/index.vue')

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: Dashboard },
    { path: '/sample', name: 'sample', component: Sample },
    { path: '/task', name: 'task', component: Task },
    { path: '/instrument', name: 'instrument', component: Instrument },
    { path: '/calibration', name: 'calibration', component: Calibration },
    { path: '/reagent', name: 'reagent', component: Reagent },
    { path: '/result', name: 'result', component: Result },
    { path: '/result/new', name: 'result-entry-create', component: ResultEntry },
    { path: '/result/:id', name: 'result-entry-edit', component: ResultEntry },
    { path: '/report', name: 'report', component: Report },
    { path: '/qc', name: 'qc', component: Qc },
    { path: '/deviation', name: 'deviation', component: Deviation },
    { path: '/sample_storage', name: 'sample_storage', component: SampleStorage },
    { path: '/contract', name: 'contract', component: Contract },
    { path: '/staff', name: 'staff', component: Staff },
    { path: '/method', name: 'method', component: Method },
    { path: '/environment', name: 'environment', component: Environment },
    { path: '/complain', name: 'complain', component: Complain },
    { path: '/audit', name: 'audit', component: Audit },
    { path: '/equipment_repair', name: 'equipment_repair', component: EquipmentRepair },
    { path: '/document', name: 'document', component: Document },
  ],
})

export default router
