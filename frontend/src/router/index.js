import { createRouter, createWebHistory } from 'vue-router'
import RobotViewer from '../views/RobotViewer.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      // This route handles both the main page and URLs like /robot_viewer?order_id=...
      path: '/robot_viewer',
      name: 'robot-viewer',
      component: RobotViewer,
      // This allows passing query parameters as props to the component.
      // For example, ?order_id=123 will become an 'order_id' prop in RobotViewer.
      props: route => ({ order_id: route.query.order_id })
    },
    {
      path: '/',
      redirect: '/robot_viewer'
    }
  ]
})

export default router
