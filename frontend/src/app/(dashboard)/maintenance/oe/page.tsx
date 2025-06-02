'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ShoppingCart, Truck, Settings, Calculator } from "lucide-react";
import Link from "next/link";

export default function OEMaintenancePage() {
  const setupItems = [
    {
      title: "Order Configuration",
      description: "Configure order types, statuses, and workflows",
      icon: ShoppingCart,
      href: "/maintenance/oe/config",
      color: "text-blue-600"
    },
    {
      title: "Shipping Methods",
      description: "Setup shipping carriers and delivery options",
      icon: Truck,
      href: "/maintenance/oe/shipping",
      color: "text-green-600"
    },
    {
      title: "Pricing Rules",
      description: "Configure pricing tiers and discount structures",
      icon: Calculator,
      href: "/maintenance/oe/pricing",
      color: "text-purple-600"
    },
    {
      title: "Order Templates",
      description: "Manage order and shipping document templates",
      icon: Settings,
      href: "/maintenance/oe/templates",
      color: "text-orange-600"
    }
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Order Entry Setup</h1>
        <p className="text-muted-foreground">
          Configure order processing, shipping, and pricing policies
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
            Essential OE setup tasks to complete before processing orders
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="space-y-2">
            <li className="flex items-center space-x-2">
              <div className="w-2 h-2 bg-green-500 rounded-full"></div>
              <span>Configure sales accounts in Chart of Accounts</span>
            </li>
            <li className="flex items-center space-x-2">
              <div className="w-2 h-2 bg-yellow-500 rounded-full"></div>
              <span>Set up order types and approval workflows</span>
            </li>
            <li className="flex items-center space-x-2">
              <div className="w-2 h-2 bg-yellow-500 rounded-full"></div>
              <span>Configure shipping methods and carriers</span>
            </li>
            <li className="flex items-center space-x-2">
              <div className="w-2 h-2 bg-red-500 rounded-full"></div>
              <span>Define pricing rules and discount policies</span>
            </li>
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
