<script lang="ts">
  import { Tabs as TabsPrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';

  type Props = TabsPrimitive.TriggerProps & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<TabsPrimitive.Trigger
  class={cn(
    'inline-flex items-center justify-center whitespace-nowrap rounded-md px-3 py-1 text-sm font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow',
    className
  )}
  {...restProps}
>
  {@render children?.()}
</TabsPrimitive.Trigger>
