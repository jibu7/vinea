'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Building2, CreditCard, Settings, FileText } from "lucide-react";
import Link from "next/link";

export default function APMaintenancePage() {
  const setupItems = [
    {
      title: "Supplier Management",
      description: "Create and manage supplier master data",
      icon: Building2,
      href: "/maintenance/ap/suppliers",
      color: "text-blue-600"
    },
    {
      title: "Payment Terms",
      description: "Configure payment terms and vendor policies",
      icon: CreditCard,
      href: "/maintenance/ap/terms",
      color: "text-green-600"
    },
    {
      title: "AP Configuration",
      description: "Setup AP module preferences and defaults",
      icon: Settings,
      href: "/maintenance/ap/config",
      color: "text-purple-600"
    },
    {
      title: "Document Templates",
      description: "Manage purchase order and payment templates",
      icon: FileText,
      href: "/maintenance/ap/templates",
      color: "text-orange-600"
    }
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Accounts Payable Setup</h1>
        <p className="text-muted-foreground">
          Configure supplier data, payment terms, and AP module settings
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
            Essential AP setup tasks to complete before processing transactions
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="space-y-2">
            <li className="flex items-center space-x-2">
              <div className="w-2 h-2 bg-green-500 rounded-full"></div>
              <span>Configure AP accounts in Chart of Accounts</span>
            </li>
            <li className="flex items-center space-x-2">
              <div className="w-2 h-2 bg-yellow-500 rounded-full"></div>
              <span>Set up supplier master data</span>
            </li>
            <li className="flex items-center space-x-2">
              <div className="w-2 h-2 bg-yellow-500 rounded-full"></div>
              <span>Define payment terms and approval workflows</span>
            </li>
            <li className="flex items-center space-x-2">
              <div className="w-2 h-2 bg-red-500 rounded-full"></div>
              <span>Configure purchase order numbering sequences</span>
            </li>
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
