import {redirect} from "next/navigation";
import {requireContext} from "@/lib/data";
export default async function Home(){const c=await requireContext();redirect(["platform_admin","auditor"].includes(c.role)?"/inbox":"/overview");}
