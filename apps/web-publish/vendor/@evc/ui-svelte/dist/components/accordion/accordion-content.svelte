<script lang="ts">
  import { Accordion as AccordionPrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';

  type Props = AccordionPrimitive.ContentProps & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<AccordionPrimitive.Content
  class="overflow-hidden text-sm transition-all data-[state=closed]:animate-accordion-up data-[state=open]:animate-accordion-down"
  {...restProps}
>
  <div class={cn('pb-4 pt-0', className)}>
    {@render children?.()}
  </div>
</AccordionPrimitive.Content>
