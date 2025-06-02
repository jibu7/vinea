'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Users, CreditCard, Settings, FileText } from "lucide-react";
import Link from "next/link";

export default function ARMaintenancePage() {
  const setupItems = [
    {
      title: "Customer Management",
      description: "Create and manage customer master data",
      icon: Users,
      href: "/maintenance/ar/customers",
      color: "text-blue-600"
    },
    {
      title: "Payment Terms",
      description: "Configure payment terms and credit policies",
      icon: CreditCard,
      href: "/maintenance/ar/terms",
      color: "text-green-600"
    },
    {
      title: "AR Configuration",
      description: "Setup AR module preferences and defaults",
      icon: Settings,
      href: "/maintenance/ar/config",
      color: "text-purple-600"
    },
    {
      title: "Document Templates",
      description: "Manage invoice and statement templates",
      icon: FileText,
      href: "/maintenance/ar/templates",
      color: "text-orange-600"
    }
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Accounts Receivable Setup</h1>
        <p className="text-muted-foreground">
          Configure customer data, payment terms, and AR module settings
        </p>
      </div>

      <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-2">
        {setupItems.map((item) => {
          const IconComponent = item.icon;
          return (
            <Card key={item.href} className="hover:shadow-md transition-shadow">
              <CardHeader className="flex flex-row items-center space-y-0 pb-2">
                <div className={`p-2 rounded-lg bg-gray-100 ${item.color}`}>
                  <IconComponent className="h-6 w-6" />
                </div>
                <div className="ml-4">
                  <CardTitle className="text-base">{item.title}</CardTitle>
                  <CardDescription className="text-sm">
                    {item.description}
                  </CardDescription>
                </div>
              </CardHeader>
              <CardContent>
                <Link href={item.href}>
                  <Button variant="outline" className="w-full">
                    Configure
                  </Button>
                </Link>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Setup Checklist</CardTitle>
          <CardDescription>
            Essential AR setup tasks to complete before processing transactions
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="space-y-2">
            <li className="flex items-center space-x-2">
              <div className="w-2 h-2 bg-green-500 rounded-full"></div>
              <span>Configure AR accounts in Chart of Accounts</span>
            </li>
            <li className="flex items-center space-x-2">
              <div className="w-2 h-2 bg-yellow-500 rounded-full"></div>
              <span>Set up customer master data</span>
            </li>
            <li className="flex items-center space-x-2">
              <div className="w-2 h-2 bg-yellow-500 rounded-full"></div>
              <span>Define payment terms and credit limits</span>
            </li>
            <li className="flex items-center space-x-2">
              <div className="w-2 h-2 bg-red-500 rounded-full"></div>
              <span>Configure invoice numbering sequences</span>
            </li>
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
