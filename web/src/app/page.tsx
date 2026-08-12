import { redirect } from 'next/navigation'

/** Landing → the login / role-select stub. */
export default function Home() {
  redirect('/login')
}
